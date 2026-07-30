---
title: "036 — Unified Snapshot Filter in Single-Tenant Path"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 036 — Unified Snapshot Filter in Single-Tenant Path

**Status**: ACCEPTED
**Date**: 2026-06-18
**Extends**: [031-run-based-source-snapshot.md](031-run-based-source-snapshot.md)

## Context

ADR-031 introduced `SnapshotFilterNode` and wired it into
`TenantRunner._build_runtime_builder` (`application/tenant_runner.py:890-902`).
The single-tenant path (`PipelineBuilder.build_nodes()`, the CLI
`job_ftch run --config`, the FastAPI/Dagster/FastStream adapters that call
`run_pipeline_from_settings`) does **not** add the node. As a result, every
single-tenant run processes all historical items again.

Two execution paths with different semantics is a real bug:

- README quickstart (`uv run job_ftch run --config /tmp/tenant.yaml`) — no
  snapshot filter, no run_id bookkeeping, repeated LLM extraction cost on
  every run.
- `tenants/*` directory mode — snapshot filter, per-run id, TTL purge, correct.

A user reading the README and a user reading the tenants docs get two different
products. ADR-031 named the bug ("run-based snapshot") but stopped at the
multi-tenant path because that was the path under active development.

## Decision

1. `PipelineBuilder` gains `with_snapshot_filter(store, run_id=None, *, tenant_id="default")`.
2. `PipelineBuilder.build_nodes()` adds `SnapshotFilterNode` as the **second**
   stage, immediately after `SanitizeNode` and before `SourceContextNode`. This
   matches `tenant_runner._build_runtime_builder` ordering exactly.
3. `run_pipeline_from_settings()` (the single-tenant CLI entry point in
   `application/builder.py:669-711`):
   - Generates `run_id = uuid4().hex` if not passed.
   - Creates the `SnapshotFilterNode` and binds it via `with_snapshot_filter`.
   - After `pipeline.run()` returns, calls `await snapshot_filter.save_and_purge()`
     so the run is persisted and the 7-day TTL is applied.
4. The Pipeline orchestrator no longer needs to be aware of snapshot filter
   wiring; `PipelineBuilder.build()` receives the same `Sequence[Stage]` list
   shape, just with one extra stage prepended.
5. Contract test: `tests/application/test_builder_uses_snapshot.py` asserts that
   `run_pipeline_from_settings(settings)` produces a pipeline whose
   `stages[1]` is an instance of `SnapshotFilterNode` and that
   `snapshot_filter.run_id` matches `summary.source_run_id`.
6. The README pipeline diagram and runtime config source-of-truth files are updated to state
   that **both** single-tenant and multi-tenant paths use the same node graph
   order: `Sanitize → SnapshotFilter → SourceContext → …`.

## Consequences

- (+) Single source of truth: there is exactly one pipeline node graph; the
  multi-tenant and single-tenant paths differ only in how they obtain the store
  and `run_id`.
- (+) Cold-start single-tenant runs are now correctly de-duplicated.
- (+) ADR-031's "run-based snapshot" is fully implemented across all entry
  points, not just `tenants/*`.
- (-) `run_pipeline_from_settings` now does one extra async call after the run
  (`save_and_purge`). Trivial in cost; documented in API.
- (-) Tests that built pipelines without a store (e.g. the `--help` smoke test)
  need either an in-memory store or a "skip snapshot filter" flag. Default is
  in-memory store via the ADR-034 auto-fallback.
- (-) The existing README diagram (line 166-168) said "snapshot filter sits
  between Dedup and SemanticPrefilter". That was wrong; it sits between
  Sanitize and SourceContext. The diagram and docs are corrected.
