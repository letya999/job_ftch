---
title: '034 — Store Backend Auto-Fallback (`store_backend="auto"`)'
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 034 — Store Backend Auto-Fallback (`store_backend="auto"`)

**Status**: ACCEPTED
**Date**: 2026-06-18
**Extends**: [020-registry-fallback-named-backend.md](020-registry-fallback-named-backend.md)

## Context

`Settings.store_backend` defaults to `"postgres"` (`config.py:18`). The
`validate_postgres_dsn` model validator (line 192-200) raises `ValueError` at
construction time if `store_dsn` is empty. Concretely:

- `python -c "from job_ftch.config import Settings; print(Settings().store_backend)"`
  raises before printing, because Settings() with no env cannot materialise a DSN.
- `uv run job_ftch --help` goes through `get_settings()` in `cli.py:530` and fails.
- New users following the README quickstart without a Postgres DSN in `.env.dev`
  cannot run any command, including `--help`, `eval`, or `test`.

ADR-020 already routed the **error-fallback path** through the named registry
(`create_store_with_fallback(settings)` resolves the fallback store by name
`"memory"`). But the **happy-path default** still demands a real Postgres DSN.
The two paths are inconsistent: a failing Postgres silently falls back to
memory, but a healthy dev box with no Postgres at all cannot even initialise
`Settings`.

The fix is the same shape ADR-020 used: route the default through a named
resolution function, never a hard-coded "postgres" string in Settings.

## Decision

1. `Settings.store_backend` default becomes `"auto"` (was `"postgres"`).
2. `Settings.store_backend` accepts the values `"auto"`, `"postgres"`, `"sqlite"`,
   `"memory"`, plus any backend name registered via `@register_store`.
3. `validate_postgres_dsn` is removed. Instead, the validator only warns
   (`log.warning`) when `store_backend="postgres"` and DSN is empty.
4. New `resolve_store_backend(settings) -> str` in `application/registry.py`:
   - If `store_backend` is one of the explicit names, return it as-is.
   - If `store_backend == "auto"`:
     - If `store_dsn` is set and the DSN parses as a real URL → return `"postgres"`.
     - Else if `JOB_FTCH_SQLITE_PATH` env or `./data/jobs.db` is writable → return `"sqlite"`.
     - Else return `"memory"`.
   - Resolution is logged at `INFO` level: `resolved store_backend=… from auto`.
5. `create_store_with_fallback` (ADR-020) keeps its error-fallback role for runtime
   failures (e.g., Postgres is reachable at construction but goes down mid-run).
6. `Settings()` instantiates without env, so `uv run job_ftch --help` works.
7. The five adapter CLIs (`telegram_bot`, `mcp`, `fastapi`, `faststream`, `dagster`)
   inherit the same auto-resolution via `get_settings()`.

## Consequences

- (+) Zero-config onboarding: `uv run job_ftch --help`, `eval`, `test` all work
  with no `.env` file. Memory store is the silent default for ephemeral runs.
- (+) Production stays explicit: operators set `JOB_FTCH_STORE_BACKEND=postgres`
  and a real DSN; no accidental fallback to memory.
- (+) ADR-020's named-fallback discipline is now the only path — no special
  import in `application/registry.py`, no hardcoded default in `Settings`.
- (-) One more env var (`JOB_FTCH_SQLITE_PATH`) to document.
- (-) Operators who relied on the `ValueError` to catch missing DSN config
  will now see a silent fallback. Mitigated by `INFO`-level log on every run.
- (-) Tests that asserted on `Settings(store_backend="postgres")` construction
  failure need to switch to asserting on resolution via `resolve_store_backend`.
