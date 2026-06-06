# 010 — PostgreSQL Store First

## Status

Accepted.

## Context

The pipeline needs persistent idempotency before production ingestion, extraction,
and publishing can be trusted. The store must persist processed raw IDs, dedup
keys, source cursors, run summaries, rejection records, and later job output
state across process restarts.

The first persistent store choice should match the expected MVP deployment
shape. Local single-process storage is useful for debug runs, but it is not the
right production boundary for concurrent ingestion, team operations, or server
deployment.

## Decision

Use PostgreSQL as the first production persistent store backend.

`PostgresStore` uses `psycopg` directly behind the `Store` port. The project does
not introduce SQLAlchemy or another ORM at this stage. Keeping SQL explicit
preserves the current ports-and-adapters architecture and keeps persistence
behavior inspectable.

The in-memory store remains the default for no-secret local fixture runs and
tests. Selecting the PostgreSQL backend requires `JOB_FTCH_POSTGRES_DSN`.

## Consequences

- (+) Production idempotency uses a deployment-grade database from the start.
- (+) Atomic insert semantics are available through PostgreSQL constraints and
  `ON CONFLICT`.
- (+) Source cursors, rejections, summaries, and future job output state can be
  shared across processes and deployments.
- (+) The adapter remains small and explicit; no heavy ORM is introduced.
- (-) Local persistent runs now require a PostgreSQL instance instead of a single
  local file.
- (-) Configuration must treat the PostgreSQL DSN as secret-bearing.
- (-) Test strategy needs fakes or integration fixtures instead of file-backed
  database checks.

## Verification

- `tests/test_postgres_store.py` covers the adapter contract with a fake
  psycopg-style connection.
- `tests/test_config.py` verifies that `postgres` store backend requires
  `JOB_FTCH_POSTGRES_DSN`.
