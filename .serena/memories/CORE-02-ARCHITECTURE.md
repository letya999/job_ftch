<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: docs/architecture.md, docs/mvp_implementation_plan.md, docs/rules.md, docs/adr/, app.py, domain/, application/, infrastructure/, nodes/, sinks/
Area: CORE
-->

# CORE-02-ARCHITECTURE

## Purpose

Describe the current architecture and boundaries for safe changes.

## Source Of Truth

- `docs/architecture.md`: hexagonal Ports & Adapters architecture and five Protocol ports.
- `docs/adr/001-hexagonal-architecture.md`: accepted decision for hexagonal architecture.
- `docs/adr/002-ddd-lite.md`: accepted decision for DDD-lite vocabulary.
- `docs/adr/003-input-quarantine-flow.md`: accepted decision for explicit quarantine flow.
- `docs/adr/004-pipeline-outcomes-and-stage-transition.md`: accepted decision for `NodeOutcome`, `PipelineStage`, `RejectReason`, `ProcessingContext`, and sink finalization.
- `docs/adr/005-postgresql-store-first.md`: accepted decision for PostgreSQL as the first production persistent store.
- `docs/mvp_implementation_plan.md`: current architecture hardening order before broad MVP feature work.
- `app.py`: composition root for all active adapters, nodes, sinks, and stores.

## Entry Points

- `app.py:build_source`: selects source adapter from `Settings.source_backend`.
- `app.py:build_nodes`: builds the active node chain: `SanitizeNode -> ValidateRawNode -> OriginPolicyNode`.
- `app.py:build_sink`: selects the primary output sink.
- `app.py:build_quarantine_sink`: selects quarantine output.
- `app.py:build_store`: selects `InMemoryStore` or `PostgresStore` from `Settings.store_backend`.

## Current Behavior

Layers:

- `domain/`: pure Pydantic models and enums.
- `application/`: Protocol ports, processing outcomes/context, pipeline orchestration, pipeline failure handlers, run summary, rejections, logging, telemetry.
- `infrastructure/`: external adapters for fixture files, Telegram, career-site HTTP/HTML, plus in-memory and PostgreSQL stores.
- `nodes/`: processing steps implementing `Node`; current runtime has sanitation, raw validation, and origin policy nodes.
- `sinks/`: output adapters implementing `Sink`; currently `JsonFileSink`.
- `app.py`: runtime wiring and CLI entry point.

## Contracts And Data

Five ports are implemented in `application/contracts.py`:

- `Source.fetch() -> AsyncIterator[SourceItem | QuarantinedRawItem]`.
- `Node.process(item, context) -> NodeOutcome[T]` plus `name`, `stage`, and `is_sanitize`.
- `Sink.emit(item)` and `Sink.finalize()`.
- `Store` processed-ID, dedup-key, source-cursor, run-summary, and rejection persistence methods.
- `LLMProvider.extract(text, schema)`; declared but no concrete adapter exists.

Current store implementations:

- `InMemoryStore`: test/local process state.
- `PostgresStore`: `psycopg` persistent backend with `processed_raw_items`, `dedup_keys`, `source_cursors`, `jobs`, `run_summaries`, and `rejections` tables.

## Invariants

- Infrastructure adapters must not contain business rules that belong in domain or nodes.
- `domain/` must remain free of I/O and third-party imports except `pydantic`.
- Processing chains must start with `SanitizeNode`; `Pipeline.__init__` enforces this.
- Source adapters should emit valid `RawItem` records or explicit `QuarantinedRawItem` records.
- Node drops, quarantines, and failures must use structured outcomes with stable stage/reason values.
- Store methods that combine check and insert must use atomic insert semantics in persistent implementations.

## Change Rules

- Add a new source under `infrastructure/sources/` and implement `Source`.
- Add a new processing step under `nodes/` and implement `Node`.
- Add a new output under `sinks/` and implement `Sink`.
- Add a new store under `infrastructure/stores/` and implement `Store`.
- Add a new LLM backend under `infrastructure/llm/` and implement `LLMProvider`.

## Verification

- `uv run pytest tests/test_contracts.py tests/test_pipeline.py`: verifies Protocol runtime compatibility and pipeline wiring semantics.
- `uv run pytest tests/test_validate_raw.py tests/test_origin_policy.py tests/test_postgres_store.py`: verifies the latest raw protection and persistent store contracts.
- `uv run mypy domain application infrastructure nodes sinks config.py`: verifies typed layer interfaces in CI scope.

## Known Gaps

- Dedup nodes, triage, LLM extraction, rejected/review/summary production sinks, and `Job` output are roadmap items, not current runtime behavior.
- `LLMProvider` exists only as a Protocol.
