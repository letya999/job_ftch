# 012 — StoreConnector protocol hierarchy

**Status**: ACCEPTED
**Date**: 2026-06-07

## Context

Phase 12 replaces the `MemoryStore` with a persistent store. A naive approach would hard-code SQLite or PostgreSQL directly. The project goal is "lightweight default, scale-up option" — SQLite must work out of the box, PostgreSQL must not be required. Future engines (MySQL, DynamoDB) should be addable without touching the core.

## Decision

Three-layer hierarchy:

```
StoreConnector          (universal protocol in application/contracts.py)
  └─ SQLStoreAdapter    (DBMS-agnostic SQL adapter; abstracts connection + query execution)
       ├─ SQLiteStore   (aiosqlite; dev / self-hosted; zero infra)
       └─ PostgreSQLStore  (asyncpg; production; no ORM)
```

`StoreConnector` defines: `has_processed`, `mark_processed`, `set_run_state`, `get_run_state`, `get_cursor`, `set_cursor`.

`SQLStoreAdapter` implements all operations using parameterized SQL — no raw f-strings. Concrete stores inject a connection factory and schema prefix.

`PostgreSQLStore` uses `asyncpg` directly (not SQLAlchemy) for maximum async throughput. `TenantConfig.tenant_id` is added as a key namespace prefix so multiple tenants share one schema without conflict.

The same three-layer pattern applies to `JobPersistenceBackend`:
- `SQLiteJobBackend` (FTS5, built into SQLite)
- `PostgreSQLJobBackend` (FTS with `tsvector`, optional pgvector)

## Consequences

- (+) `pip install job_ftch` works with zero additional infra (SQLite).
- (+) Scaling to PostgreSQL requires only a config change, no code change in Pipeline.
- (+) New storage engines added by implementing `StoreConnector`, not by editing core.
- (-) Two SQL dialects to maintain (SQLite + PostgreSQL). Schema migration tooling needed for upgrades.
