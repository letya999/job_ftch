# 004 - Pipeline Node Contracts and Stats Shape

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
Phase 3 introduced early triage and richer run reporting. The first implementation kept three rough edges:
- `RawItemRejected.to_quarantined()` used defensive `hasattr()` checks for fields guaranteed by `RawItem`.
- `RunSummary` and per-source stats duplicated the same counters and reason maps.
- The pipeline enforced "sanitize first" through a runtime `is_sanitize` flag on the generic `Node` protocol.

Those choices worked, but they encoded real invariants indirectly and added avoidable dead code.

## Decision
- Treat `RawItem` as a strict contract inside rejection flow and remove defensive `hasattr()` branches.
- Introduce a shared stats base dataclass for counter/reason bookkeeping, then extend it for run-level and per-source summaries.
- Split pipeline node contracts into two semantic sub-protocols:
  - `SanitizingNode` for the mandatory first sanitation step.
  - `ProcessingNode` for all subsequent steps.
- Make `Pipeline` accept `sanitize_node` separately from `nodes`, so the composition root enforces the chain shape directly.

## Consequences
- (+) The sanitize-first invariant is explicit in the constructor and composition root.
- (+) Stats bookkeeping is centralized and less error-prone.
- (+) Rejection serialization matches the actual `RawItem` contract without dead branches.
- (-) `Pipeline(...)` call sites become slightly more verbose because they pass `sanitize_node=` explicitly.
