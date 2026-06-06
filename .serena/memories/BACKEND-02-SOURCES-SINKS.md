<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 35f604c feat(persistence): add PostgreSQL store backend
Scope: infrastructure/sources/, infrastructure/stores/, sinks/, application/registry.py, docs/adr/010-postgresql-store-first.md, tests/test_postgres_store.py
Area: BACKEND
-->

# BACKEND-02-SOURCES-SINKS

## Purpose

Document external adapter boundaries for sources, sinks, and stores.

## Source Of Truth

- `infrastructure/sources/`: fixture, Telegram, career-site, and declarative
  source adapters.
- `sinks/json_file.py`: JSON/JSONL output adapter.
- `infrastructure/stores/in_memory.py`: default local/debug store.
- `infrastructure/stores/postgres.py`: production persistent store.
- `application/registry.py`: adapter registration and creation.

## Entry Points

- `@register_source(...)`, `@register_sink(...)`, `@register_store(...)`:
  backend registration decorators.
- `create_source`, `create_sink`, `create_store`: composition root helpers.
- `PostgresStore`: `psycopg` adapter selected by `store_backend=postgres`.

## Current Behavior

Built-in adapters are imported by `load_extensions()`. The in-memory store is
the default for local no-secret runs. PostgreSQL is the production persistent
store and requires `JOB_FTCH_POSTGRES_DSN`.

`PostgresStore` initializes tables on first use and stores processed IDs, dedup
keys, duplicate records, source cursors/run state, jobs, run summaries, and
rejection payloads. It uses explicit SQL, PostgreSQL constraints, `ON CONFLICT`,
and `psycopg.types.json.Jsonb` for JSONB payloads.

## Contracts And Data

- Store protocol methods: processed IDs, dedup keys, duplicate records, and run
  state.
- PostgreSQL tables: `processed_raw_items`, `dedup_keys`,
  `duplicate_records`, `source_cursors`, `jobs`, `run_summaries`,
  `rejections`.
- `JOB_FTCH_POSTGRES_DSN` is secret-bearing and required only when the
  PostgreSQL backend is selected.

## Invariants

- Do not introduce a heavy ORM for MVP persistence.
- Keep external SDK imports in infrastructure adapters.
- Keep default local runs credential-free.

## Change Rules

- Store protocol changes must update both in-memory and PostgreSQL stores.
- Add tests for adapter registration and persistence behavior when adding a
  backend.

## Verification

- `uv run pytest tests/test_postgres_store.py`: PostgreSQL adapter behavior via fake connection.
- `uv run pytest tests/test_config.py`: backend-specific configuration policy.
- `uv run pytest tests/test_sources.py`: source adapter behavior.
