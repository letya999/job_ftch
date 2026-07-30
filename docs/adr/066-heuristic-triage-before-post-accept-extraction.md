---
title: "ADR-066: Heuristic triage before post-accept extraction"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-066: Heuristic triage before post-accept extraction

**Status**: ACCEPTED
**Date**: 2026-07-15
**Extends**: ADR-056, ADR-064, ADR-065

## Context

The accepted compact relevance graph reaches `precision=.9412` and
`recall=.8889`, but still performs 100 nano extraction calls before the
terminal decision. Those calls cost `$0.0244371` and account for most of the
remaining avoidable latency even though only 17 candidates are accepted.

ADR-064 already makes full extraction an idempotent post-accept enrichment
task. The pre-decision graph only needs a typed `JobDraft` carrying source
identity, bounded raw text, trusted structured fields when available, and
enough metadata for evidence production. Requiring an LLM to populate the
same draft before relevance is therefore duplicated work.

## Decision

1. Keep `ExtractionNode` as the sole typed `RawItem -> JobDraft` boundary, but
   add an explicit YAML-selectable mode:
   - `llm_or_structured` (default and compatibility behavior);
   - `structured_or_heuristic` (cost-optimized candidate behavior).
2. In `structured_or_heuristic` mode, trusted structured source mapping still
   uses the existing zero-LLM fast path. Other observations become a partial
   heuristic draft from source metadata and the sanitized posting text.
3. Heuristic triage is a transport conversion, not relevance evidence. It
   cannot ACCEPT, REJECT, or synthesize responsibilities that are absent from
   the posting.
4. The compact responsibility judge consumes the bounded original-posting
   fallback when structured responsibilities are unavailable. `DecisionNode`
   remains the only terminal owner.
5. ACCEPT continues into the existing post-accept enrichment queue, whose
   `full_extraction` task can retry independently and cannot change relevance.
6. The default graph is unchanged. A separate candidate graph selects the new
   mode and is promoted only after a frozen 140-parent runtime replay satisfies
   `precision > .75`, `recall >= .85`, and lower measured total LLM cost.
7. Provider failure and missing fields remain partial/unknown; they are never
   converted into negative evidence.

## Expected consequences

- Pre-decision extraction calls should fall from 100 to zero for unstructured
  observations, while structured ATS/API mapping remains free.
- The measured relevance cost remains, but full extraction is paid only for
  accepted records in the asynchronous post-accept lane.
- Decision quality may change because the judge sees raw responsibility text
  rather than an LLM-normalized draft. This is why the candidate remains
  non-production until the full regression replay passes.

## Validation

The post-accept graph was replayed on 2026-07-15 against the canonical
fixed-140 v2 dataset (same selected parent IDs, tenant profile, and shot
snapshot as ADR-065). Graph hash
`4c7e0c291dcc1439efb12cbb62edeaba33f21a82aab4b6204ffff6ef7b03d907`
produced 149 candidate decisions with `TP=16`, `FP=1`, `FN=2`, `TN=130`:
`precision=.9412`, `recall=.8889`, `F1=.9143`.

The decision path made 145 compact classification calls and no extraction
calls, costing `$0.0560932` with p50 `1.233s`, p95 `2.108s`, and wall time
`193.88s`. Compared with ADR-065's pre-decision extraction baseline, measured
cost fell from `$0.0758491` by 26.05% and wall time from `509.25s` by 61.93%,
while the fixed regression quality gate stayed unchanged. Full extraction
remains a post-accept cost and is not hidden from production accounting.

The graph stays `production_default: false`. A clean 500-parent historical
replay (548 candidate observations after segmentation, 538 LLM calls, no
fallback) returned `TP=48`, `FP=15`, `FN=12`, `TN=473`: `P=.7619`,
`R=.8000`, `F1=.7805` at `$0.2125`. It is a scale signal only, not a failed
promotion comparison: it overlaps the fixed-140 input in only 65 parents and
uses the immutable historical labels. The validator currently reports 17
label-invariant violations, 76 provider-error-as-negative labels, and 181
duplicate-content groups; the generated human-adjudication queue contains 408
unique content groups. Locked holdout and append-only re-audit must complete
before this wider metric can promote or reject the graph.

## MVP rollout note (2026-07-17)

ADR-070 supersedes the mutable `production_default` rollout mechanism. The
benchmarked graph file remains byte-stable with `production_default: false`;
the runtime promotes it by configured path plus the exact validated SHA-256
above and fails closed on a mismatch. For synchronous `/run`, accepted items'
durable post-accept tasks are drained before delivery, so post-accept is no
longer an unobserved background-only cost.

The clean 14-source container canary
`7682050f072d45b58762ff43da38c1f4` produced 56 ACCEPT records and 53 persisted
groups with no source failures. Its complete provider-usage cost was
`$0.200855`; the `$0.0560932` figure remains the locked decision-path benchmark.
