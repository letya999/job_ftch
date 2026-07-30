---
title: "032 — Classification + Extraction Eval Harness (TD-002)"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 032 — Classification + Extraction Eval Harness (TD-002)

**Status**: ACCEPTED
**Date**: 2026-06-18
**Closes**: TD-002 in `docs/techdebt.md`

## Context

The pipeline has 1412 labeled examples in `fixtures/dataset/labels.jsonl`
(mix of `job_posting` / `announcement` / `unknown` from real Telegram and
career-site traffic) and 51 gold `RawItem → expected fields` records in
`fixtures/extraction/gold_samples.jsonl`. Both were sitting unused.

The existing `scripts/evaluate_extraction.py` reported `samples / matched /
expected / field_match_rate` for the 2-sample gold file but had no
classification counterpart, no per-field breakdown, no LLM-call counter, and
no CI gate. Every merge into a pipeline node was a coin-flip: regressions
in `PostTypeClassificationNode` or `ExtractionNode` would only surface
through user complaints or manual inspection.

## Decision

Adopt a two-script eval harness, both with the same shape:

1. **Classification harness** — `scripts/evaluate_classification.py`
   - Input: `--fixture fixtures/dataset/labels.jsonl` (1412 records).
   - Runs `PostTypeClassificationNode(classifier=None)` (the cheap rules
     path) on every `raw_item`.
   - Compares predicted `metadata.preclassified_post_type` against the
     labeled `post_type`.
   - Reports, per class: precision, recall, F1, support.
   - Reports overall: `accuracy`, `false_positive_rate`
     (fraction of non-`job_posting` items classified as `job_posting`),
     `valid_url_rate` (fraction of expected jobs with parseable URL),
     `llm_calls_per_100_items` (zero in rules-only mode).
   - `--gate` exit code:
     - pass ⇒ JOB_POSTING precision >= 0.9 AND FP rate <= 0.05
     - fail ⇒ exit 1, with the metrics printed to stderr.
   - Output: `artifacts/eval/classification.json`.

2. **Extraction harness** — `scripts/evaluate_extraction.py`
   - Input: `--fixture fixtures/extraction/gold_samples.jsonl` (51 records).
   - Runs the existing `ExtractionNode(llm)` over each item, compares
     `expected` fields against the produced `Job` payload.
   - Adds per-field match rate, LLM-call counter, and `--gate` exit code:
     - pass ⇒ `field_match_rate >= 0.75`
     - fail ⇒ exit 1.
   - Output: `artifacts/eval/extraction.json`.

3. **Wrapper** — `scripts/eval_all.sh`
   - Runs both harnesses with `--gate` and propagates exit code.
   - Intended for CI and pre-merge local checks.

4. **Gold fixture expansion** — `fixtures/extraction/gold_samples.jsonl`
   grew from 2 to 51 records (RU + EN, `career_site` + `telegram_*`),
   covering `title`, `work_mode`, `language`.

5. **No live-LLM dependency in CI** — the harnesses run on the heuristic
   backend by default. `field_match_rate >= 0.75` is the floor; the
   heuristic currently produces 0.81.

## Consequences

- (+) Pipeline regressions in classification or extraction now fail CI
  before merge instead of surfacing in production.
- (+) Per-class metrics make it obvious whether a regression is
  precision-side (more FP) or recall-side (more FN).
- (+) The 1412-sample dataset gives a stable baseline; future harness
  runs compare against `artifacts/eval/*.json`.
- (+) Tests cover both the in-process API and the CLI smoke (`subprocess`
  run) so the gate is itself regression-protected.
- (-) Heuristic baseline recall on `job_posting` is 0.585 (most real
  vacancies lack the obvious "ищем / вакансия" tokens). Improving it
  is a separate scope — likely a small LLM classifier behind
  `PostTypeClassificationNode.classifier` (already wired, just not the
  default).
- (-) `--gate` enforces a soft floor; tightening it (precision >= 0.95,
  recall >= 0.7) requires the LLM path to be the default. Deferred.
- (-) `gold_samples.jsonl` is hand-curated. Drift between fixture and
  production traffic will eventually make the gate useless unless we
  re-curate. Add a `scripts/refresh_gold_samples.py` later (out of scope).
