# 004 - Pipeline Outcomes and Stage Transition

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context

The pipeline started as a pass-through `RawItem` spine where nodes returned either the
same item type or `None` to drop an item. That shape is too weak for the MVP roadmap:
the system must explain why items were dropped or quarantined, report counts by stage
and reason, isolate per-item failures, finalize sinks reliably, and eventually support
the first-class transition from `RawItem` extraction input to `Job` output.

The existing quarantine flow already made source-level malformed payloads observable,
but node-level drops were still unstructured.

## Decision

Introduce explicit application-level processing outcomes:

- `NodeOutcome` represents `pass`, `drop`, `quarantine`, or `fail`.
- `PipelineStage` identifies the stage that produced an outcome.
- `RejectReason` provides stable application-level reason values for summary metrics.
- `ProcessingContext` carries small run-scoped execution context to nodes.
- `RunSummary` tracks stage, reason, and source counters and serializes timestamps as
  ISO 8601 strings.

Nodes now receive `(item, context)` and return `NodeOutcome`. The pipeline still accepts
the existing `QuarantinedRawItem` source records and still catches legacy
`RawItemRejected` exceptions so the migration remains incremental.

Sinks get a `finalize()` lifecycle method. The pipeline calls finalizers after a run even
when item processing fails.

## Consequences

- (+) Drops and quarantines have stable stages and reasons.
- (+) Future `RawItem -> Job` extraction can be represented without pushing business
  logic into sinks.
- (+) Run summaries and logs can be JSON-serializable and metrics-friendly.
- (+) Sink finalization becomes part of the application contract.
- (-) Node implementations and tests must use the richer outcome API.
