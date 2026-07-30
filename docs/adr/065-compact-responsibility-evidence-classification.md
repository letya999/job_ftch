---
title: "ADR-065: Compact responsibility evidence classification"
description: "**Status**: ACCEPTED"
updated: 2026-07-29
---
# ADR-065: Compact responsibility evidence classification

**Status**: ACCEPTED
**Date**: 2026-07-15
**Extends**: ADR-058, ADR-062, ADR-063

## Context

The production-equivalent `evidence_v2` graph already has one typed evidence
transport and one terminal `DecisionNode`, but its relevance LLM still returns
the historical binary decision schema: a self-reported confidence, free-form
reasoning, and two free-form aspect lists. On the fixed 140-parent regression
set this response consumes 24,299 completion tokens across 130 calls. The same
schema also encourages the judge to accept jobs because the company or product
uses AI, even when the candidate's actual responsibilities are product
management, business analysis, data engineering, model training, or no-code
operation.

Self-reported model confidence is not a calibrated probability under ADR-062.
Free-form explanations are useful for research traces but should not be the
runtime policy input. The terminal policy needs compact, auditable evidence
about the role and its stated responsibilities.

## Decision

1. Add a provider-neutral structured relevance-evidence response with only:
   jobness (`yes/no/unknown`), role relation (`target/adjacent/other/unknown`),
   responsibility fit (`support/contradict/unknown`), and bounded references to
   numbered vacancy evidence snippets.
2. Derive the compatibility `accept/reject/review` value deterministically from
   those fields. The model does not emit a terminal decision or confidence.
3. Materialize a positive or negative `EvidenceAtom` with fixed strength and a
   separately versioned producer reliability. `DecisionNode` remains the sole
   terminal owner.
4. Build a compact vacancy decision card from title, role fields, stated
   responsibilities, requirements, and a bounded description fallback. The
   prompt explicitly distinguishes personal target responsibilities from an AI
   company/domain mention.
5. Keep the historical response schema as a schema-v1 compatibility mode.
   YAML v2 selects the compact mode explicitly; cache keys include the
   classifier mode and schema version.
6. Provider failure, invalid output, or unknown evidence remains degradation or
   REVIEW/DEFERRED and never becomes an implicit reject.
7. No new dependency or provider is introduced. External BGE and OpenRouter
   remain future adapter changes behind the existing ports.

## Consequences

- Completion cost should fall materially without reducing the number of
  candidates judged by the LLM.
- Precision can improve because the evidence question is responsibility-first,
  while BGE remains a recall-preserving supporting signal.
- Existing schema-v1 experiments stay replayable.
- The compact output intentionally carries less human prose; auditability comes
  from stable evidence references, stored raw inputs, model provenance, and the
  deterministic decision trace.

## Validation

The production-equivalent runtime replay on 2026-07-15 used the append-only
`fixed140_seed42_v2` labels (canonical dataset SHA-256
`cd600175b52e2e05b804e19fc8dd0f9383baf6968cfa2969973ce119d196676d`), the same
140 selected parent IDs, and graph
`precision_first_v2_compact` (`4dd6423713445039f95eb03e5fab425bed105f2ff11abe7adfbd36602dabca2a`).
It produced 149 candidate decisions with `TP=16`, `FP=1`, `FN=2`, `TN=130`:
`precision=.9412`, `recall=.8889`, and `F1=.9143`.

Compared with the preceding `evidence_v2` replay, total measured LLM cost fell
from `$0.0951313` to `$0.0758491` (-20.27%). Relevance completion tokens fell
from 24,299 to 4,316 (-82.24%) and relevance cost fell from `$0.0710332` to
`$0.0514120` (-27.62%). End-to-end item latency improved from 4.814s to 2.621s
at p50 and from 9.930s to 8.896s at p95.

This accepts the compact schema and candidate graph. It does not promote the
graph to the Telegram bot and does not claim completion of the separately
deferred external BGE-M3 or OpenRouter adapter work.

## 2026-07-29 amendment: adjacent/unknown recall fallback

Production graph `precision_first_v2_compact_prefilter` version `2.6.0`
keeps the compact schema but adjusts the deterministic compatibility decision:
an `adjacent` role with `responsibility_fit=unknown` may become positive
profile evidence only when all of these are true:

- the compact judge says `is_job=yes`;
- it cites positive evidence IDs;
- it cites no negative evidence IDs;
- the item has a profile;
- the vacancy text contains a profile-specific target-role signal.

This fallback is intended for broad profiles where adjacent role labels can
still describe target work. It does not allow `other`, contradiction, or
negative-evidence cases to become ACCEPT.

MVP caveat: the guard that suppresses bare generic role words is currently a
Python-side list. That list is a documented technical debt item (`TD-030`) and
must move into a versioned ontology/profile artifact or graph parameter before
this policy is considered profile-portable.
