---
title: "057 — Hybrid retrieval and cross-encoder reranking boundary"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 057 — Hybrid retrieval and cross-encoder reranking boundary

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-028 and ADR-038 remain historical model
integration notes. They do not define a terminal policy fallback.

## Context

Dense, sparse, lexical and cross-encoder scores have different semantics. A
missing reranker previously silently fell through to unrelated legacy scores
and could turn an otherwise eligible candidate into a rejection.

## Decision

Retriever raw features are preserved and fused separately from policy. A
cross-encoder reranker is a top-K refinement stage; its unavailability is an
explicit degradation artifact. When routing is configured to depend on a
reranker threshold and no calibrated reranker score is available, output is
`REVIEW`, never a silent `REJECT`.

RRF is the initial fusion baseline. Calibrated/learned fusion may replace it
only with held-out source/time evidence and must retain raw component scores.

## Consequences

- Provider/model outages are observable policy inputs.
- Reranker does not shrink the retrieval candidate set.
- Thresholds cannot claim calibration for a fabricated fallback score.
