<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: application/pipeline.py, application/contracts.py
Area: PIPELINE
-->

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
- After Phase 4, raw-item idempotency is based on `domain.processed_key_for_raw_item(...)`, not the raw `stable_id`.
- `Pipeline` should mark processed keys for all terminal outcomes (`emit`, duplicate drop, quarantine, explicit drop) so reruns stay idempotent.
- `DedupNode` is a normal `ProcessingNode` placed after `HeuristicTriageNode`; it owns exact URL/content dedup and fuzzy near-duplicate checks.
- `Store` now persists remembered dedup keys and duplicate explanation records, so future store backends must preserve this behavior.
- `RawItemRejected.to_quarantined()` assumes a real `RawItem` contract and should not reintroduce defensive `hasattr()` checks.
- When editing pipeline/reporting logic later, keep `SanitizeNode` first and preserve stage counters `fetched -> sanitized -> triaged -> emitted`, plus per-source drop/quarantine reasons.
- Current planning direction extends the funnel beyond early triage:
  source context, post-type classification, hard filter, dedup candidate lookup, semantic prefilter,
  extraction, normalization, aggregation, match scoring, risk/quality, routing.
- Old mental model "RawItem -> Job is the only future shape" is now too coarse for planning.
  Target contract family should converge toward `RawItem -> JobDraft -> JobRecord -> JobGroup`,
  while still keeping the extraction boundary as the main raw-to-structured transition.
- Preserve explicit counters and reason maps even if the funnel becomes deeper.
  Additional stages must not collapse observability for:
  relevance drops, risk reviews, quality reviews, duplicate handling, and aggregation routing.
