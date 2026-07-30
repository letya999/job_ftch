---
title: "051 — Production-equivalent evaluation and graph contract"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 051 — Production-equivalent evaluation and graph contract

**Status**: ACCEPTED
**Date**: 2026-07-09
**Supersedes/Transitions**: ADR-031, ADR-036, ADR-041, ADR-042 and ADR-044 remain historical runtime descriptions until their replacement phases are implemented.

## Context

`PIPELINE_REDESIGN_PLAN.md` changes the pipeline from a single relevance score
to independently measurable lifecycle, evidence, retrieval and policy stages.
Before changing those stages, the currently built graph and its optional
branches must be observable and testable. Otherwise an ADR, settings field or
threshold can describe a producer that is absent at runtime.

The existing evaluation dataset is relevance-oriented and does not yet preserve
the complete source observation envelope. It is therefore a baseline input, not
proof that a redesign is production-equivalent.

## Decision

1. `build_nodes()` is the graph composition authority. A graph-contract test
   records the ordered default graph and the boundaries for sanitize, snapshot,
   extraction, relevance judge, presentation, routing and canonical group
   mutation.
2. Optional policy consumers must declare their required artifact. Enabling
   reranker routing thresholds without a stage that declares production of
   `bge_reranker_max_score` is invalid configuration and fails during graph
   construction.
3. Phase 0 evaluation inputs will preserve the original raw observation and
   source envelope. Synthetic URL or metadata may not be added merely to make a
   replay pass.
4. Baseline reports are descriptive only. No threshold sweep or model change is
   considered a production decision until stage labels and source/time slices
   exist.

## Consequences

- (+) Runtime/configuration drift becomes a local deterministic test failure.
- (+) Later phases can replace a graph edge deliberately with a corresponding
  contract update and replay evidence.
- (-) The contract documents the current graph, including known temporary
  ordering defects; it does not ratify them as the target architecture.
- (-) The first baseline report will be incomplete until the raw-envelope and
  human-adjudicated datasets in Phase 0 are added.
