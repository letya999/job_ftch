# Pipeline Contracts And Stats

Updated after Phase 3 follow-up cleanup on 2026-06-06.

- See ADR `docs/adr/004-pipeline-node-contracts-and-stats.md`.
- `Pipeline` no longer uses the runtime `is_sanitize` flag.
- The sanitize-first invariant is encoded by the constructor shape:
  - `sanitize_node: SanitizingNode[T]`
  - `nodes: Sequence[ProcessingNode[T]]`
- `app.build_nodes()` returns `(sanitize_node, processing_nodes)` and the composition root wires them separately.
- Shared counters/reason maps live in `application.pipeline.StatsBase`.
  - `RunSummary` extends it for whole-run stats.
  - `SourceRunStats` extends it for `by_source_kind` breakdown.
- `RawItemRejected.to_quarantined()` assumes a real `RawItem` contract and should not reintroduce defensive `hasattr()` checks.
- When editing pipeline/reporting logic later, keep `SanitizeNode` first and preserve stage counters `fetched -> sanitized -> triaged -> emitted`, plus per-source drop/quarantine reasons.