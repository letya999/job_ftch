<!-- Memory Metadata
Last updated: 2026-06-06
Last commit: 074ae27 feat(pipeline): add PostgreSQL-backed foundation
Scope: infrastructure/sources/, infrastructure/stores/in_memory.py, infrastructure/stores/postgres.py, sinks/json_file.py, fixtures/, tests/test_sources.py, tests/test_store.py, tests/test_postgres_store.py
Area: BACKEND
-->

# BACKEND-02-SOURCES-SINKS

## Purpose

Document implemented adapters and their data mapping, sink, and store behavior.

## Source Of Truth

- `infrastructure/sources/local_fixture.py`: local JSON and JSONL fixture source.
- `infrastructure/sources/raw_item_factory.py`: shared `RawItem` construction for real sources.
- `infrastructure/sources/telegram.py`: Telegram channel, group, and comment sources.
- `infrastructure/sources/career_site.py`: Greenhouse and BCC career-site parsing.
- `infrastructure/stores/in_memory.py`: in-memory processed IDs, dedup keys, cursors, summaries, and rejection state.
- `infrastructure/stores/postgres.py`: PostgreSQL persistent store backend through `psycopg`.
- `sinks/json_file.py`: JSON array and JSONL output with sink finalization.
- `fixtures/`: debug, e2e, and sanitized real-world source fixtures.
- `tests/test_sources.py`: adapter mapping tests.
- `tests/test_store.py`: in-memory store idempotency tests.
- `tests/test_postgres_store.py`: persistent store tests with a fake psycopg-style connection.

## Entry Points

- `LocalFixtureSource.fetch`: yields valid `RawItem` or source-level `QuarantinedRawItem`.
- `build_raw_item`: canonical helper used by Telegram and career-site adapters.
- `TelegramChannelSource.fetch`, `TelegramGroupSource.fetch`, `TelegramCommentSource.fetch`: read Telethon history.
- `CareerSiteSource.fetch`: fetches board HTML, selects parser, and yields career-site `RawItem` records.
- `JsonFileSink.emit`: writes model payloads as JSON array or JSONL.
- `PostgresStore`: selected by `JOB_FTCH_STORE_BACKEND=postgres` and `JOB_FTCH_POSTGRES_DSN`.

## Current Behavior

Local fixtures support JSON arrays and JSONL. Invalid JSON, non-object records, and records failing `RawItem` validation become `QuarantinedRawItem` records so later valid records can continue.

Telegram sources map message text, IDs, dates, chat metadata, sender metadata, views, forwards, grouped ID, and reply lineage into `RawItem`. Comment source iterates channel posts, then comments for each post, and stores `post_message_id` and `post_url`.

Career-site source supports Greenhouse boards and BCC career pages. Greenhouse parsing uses board HTML links; BCC parsing fetches each detail page up to the configured limit.

`JsonFileSink` creates parent directories. JSONL mode truncates the output file on construction and appends one sorted UTF-8 JSON object per emit with `ensure_ascii=False`. JSON array mode buffers emitted payloads in memory and writes once during `finalize()` via a temp file and atomic `Path.replace()`. `finalize()` is idempotent.

`InMemoryStore` and `PostgresStore` both implement the expanded `Store` protocol. `PostgresStore` initializes schema on first use and uses PostgreSQL constraints plus `ON CONFLICT` for atomic processed-ID and dedup-key semantics. Source cursors, run summaries, and rejection payloads are persisted in PostgreSQL tables.

## Contracts And Data

`build_raw_item` removes metadata keys whose values are `None`, normalizes timestamps to UTC, and delegates final validation to `RawItem.model_validate`.

`PostgresStore` tables: `processed_raw_items`, `dedup_keys`, `source_cursors`, `jobs`, `run_summaries`, and `rejections`.

Current `Pipeline` still uses processed IDs directly. Dedup APIs and PostgreSQL job persistence helpers exist for later dedup/job-output slices, but no dedup node uses them yet.

## Invariants

- Source adapters should use `build_raw_item` where practical to keep the canonical raw shape consistent.
- Source adapters must not bypass `RawItem` invariants with `model_construct`.
- JSON output paths under `artifacts/` are ignored runtime artifacts.
- Sinks must implement idempotent `finalize()`.
- Persistent store check-and-insert operations must be atomic.
- Telethon session files are ignored runtime artifacts.

## Change Rules

- Add source-specific regression fixtures before changing real-world parsing behavior.
- Keep external integration tests offline by using recorded fixtures and fakes.
- Preserve JSONL and JSON array modes unless the public contract is intentionally changed.
- When changing Store protocol, update both `InMemoryStore` and `PostgresStore` plus contract tests.

## Verification

- `uv run pytest tests/test_sources.py`: Telegram, Greenhouse, BCC, and raw factory mapping.
- `uv run pytest tests/test_store.py`: in-memory store idempotency.
- `uv run pytest tests/test_postgres_store.py`: PostgreSQL store contract and idempotency.
- `uv run pytest tests/test_pipeline.py`: sink behavior through pipeline integration.
- `uv run pytest tests/test_json_sink.py`: direct JSON sink finalization and UTF-8 behavior.

## Known Gaps

- `JsonFileSink` has no schema version field.
- Dedup APIs exist in `Store` but no dedup node currently uses them.
- Source cursor advancement and dry-run cursor mutation policy are not fully implemented in source adapters yet.
