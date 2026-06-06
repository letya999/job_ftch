<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: docs/architecture.md, docs/adr/, application/contracts.py, application/pipeline.py, application/registry.py, app.py
Area: CORE
-->

# CORE-02-ARCHITECTURE

## Purpose

Document the current ports-and-adapters architecture and the contracts future
implementation must preserve.

## Source Of Truth

- `application/contracts.py`: `Source`, `Stage`, `SanitizingNode`,
  `ProcessingNode`, `Sink`, `FlushableSink`, `Store`, and `LLMProvider` ports.
- `application/pipeline.py`: async source-to-stage-to-sink orchestration,
  `RunSummary`, source quarantine handling, and sink flushing.
- `application/registry.py`: self-registration for sources, sinks, stores, and
  career-site parsers.
- `docs/adr/010-postgresql-store-first.md`: PostgreSQL is the production
  persistent store backend.

## Entry Points

- `Pipeline.__init__`: accepts one mandatory sanitize node, optional processing
  stages, one sink or a sink sequence, a store, and optional quarantine sink.
- `Pipeline.run`: processes async source items, records stats, emits output, and
  flushes flushable sinks.
- `load_extensions`: imports built-in adapters and entry-point plugins.

## Current Behavior

`Source.fetch()` yields `RawItem` or `QuarantinedRawItem`. Source-level
quarantined items bypass processing nodes and go to quarantine output. Regular
items pass through the mandatory `SanitizeNode`, optional `ProcessingNode`
stages, and then the sink.

`Pipeline` marks processed IDs through the `Store` after terminal item outcomes.
It records fetched, sanitized, triaged, dropped, emitted, quarantined, failed,
and per-source counters in `RunSummary`.

## Contracts And Data

- Type transitions happen through `Stage[In, Out]`.
- Same-type runtime nodes use `SanitizingNode[T]` or `ProcessingNode[T]`.
- Buffered sinks expose `flush()` through `FlushableSink`.
- Store backends implement processed IDs, dedup keys, duplicate records, and run
  state methods defined in `application/contracts.py`.

## Invariants

- Keep `domain/` pure; infrastructure dependencies stay outside the domain.
- Do not add adapter `if/elif` dispatch in `app.py`; use registry decorators.
- Do not add Kafka, Celery, Airflow, LangChain, LangGraph, Scrapy, or heavy ORMs.

## Change Rules

- New persistent backend decisions need an ADR before code.
- New adapters should self-register and keep configuration validation in
  `Settings`.
- Update `docs/architecture.md` and this memory when port contracts change.

## Verification

- `uv run pytest tests/test_pipeline.py`: pipeline ordering, quarantine, and sink behavior.
- `uv run pytest tests/test_contracts.py`: protocol/runtime contract checks.
- `uv run mypy .`: protocol compatibility across adapters.
