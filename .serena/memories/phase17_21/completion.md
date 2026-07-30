<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: application/, infrastructure/sources/
Area: HISTORY
-->

# Phase 17-21 Completion

Completed on 2026-06-08. Branch: `phase-17-21`.

## Phase 17 — Scheduler and Daemon Mode

- `application/scheduler.py`: `Scheduler` class — asyncio loop, SIGINT/SIGTERM via `asyncio.Event`, graceful drain on stop.
- `app.py`: `--daemon` flag on `pipeline` subcommand; PID file at `.runtime/.pid`; `--status` reads last run from store.
- `config.py`: `schedule_interval_seconds: int | None` setting.
- Interval resolution: min across per-source `interval_seconds`, else settings global, else 3600.
- `RunSummary` gained `scheduled_run_index: int` and `source_run_id: str | None`.

## Phase 18 — Auth and Ingest

- `AuthProvider` protocol in `application/contracts.py` (already existed, extended).
- `infrastructure/auth/env_auth.py`: reads `JOB_FTCH_AUTH_{SOURCE_ID}_{KEY}` env vars.
- `infrastructure/auth/file_auth.py`: lazy-loads secrets from YAML file.
- `infrastructure/ingest/polling.py`: `PollingMode` implementing `IngestMode` protocol.
- `IngestMode` protocol added to `application/contracts.py`.

## Phase 19 — Official REST API Sources

- `domain/source_spec.py`: `RestAPISourceSpec` (already existed from phase 16), added `auth_source_id: str | None`.
- `infrastructure/sources/api/base.py`: `OfficialAPISource` — pagination, auth header injection, field mapping, incremental cursor via Store.
- `infrastructure/sources/api/greenhouse.py`: Greenhouse ATS adapter with default field map.
- `infrastructure/sources/api/hh.py`: HH.ru adapter with default field map.
- Factory pattern (not class decorators) for `@register_source_v2` — `SourceSpecFactory = Callable[[Any, AuthProvider], object]` is 2-arg; classes have 3-arg `__init__`.
- `fixtures/api/greenhouse_sample.json`, `fixtures/api/hh_sample.json` for contract tests.

## Phase 20 — Browser Source Stub

- `domain/source_spec.py`: `BrowserSourceSpec` added to discriminated union.
- `infrastructure/sources/browser/base.py`: `BrowserSource` stub — guards `playwright` import, raises `ImportError`/`NotImplementedError`.
- Registered as `"browser"` in `load_extensions()`.

## Phase 21 — Realtime and Push Ingestion

- `domain/source_spec.py`: `RSSFeedSourceSpec`, `TelegramRealtimeSourceSpec`, `WebhookSourceSpec`, `WebSocketSourceSpec`.
- `infrastructure/sources/realtime/rss.py`: `RSSFeedSource` — HTTP polling, feedparser in thread pool, incremental seen-ID dedup via `Store.get_run_state/set_run_state`, 10k cap to prevent unbounded growth.
- `infrastructure/sources/telegram_realtime.py`: `TelegramRealtimeSource` — Telethon `NewMessage` handler, infinite generator, stop via `asyncio.Event`.
- `infrastructure/sources/realtime/webhook.py`, `websocket.py`: stubs raising `NotImplementedError`.
- `fixtures/feeds/sample_feed.xml`: RSS fixture for tests.
- `feedparser` added to `pyproject.toml` optional deps `[feeds]` group.

## ADR-020 — Registry Fallback via Named Backend (Layer Boundary Fix)

**Critical fix**: `application/registry.py::create_store_with_fallback` had a direct import
`from infrastructure.stores.in_memory import InMemoryStore` — violating hexagonal layer rules.

Fix: resolve fallback through the existing store registry by name:
```python
_FALLBACK_STORE_BACKEND = "memory"  # registered key in _store_factories

def _create_fallback_store(settings: Settings) -> object:
    factory = _store_factories.get(_FALLBACK_STORE_BACKEND)
    if factory is None:
        raise RuntimeError(...)
    return factory(settings)
```

Pattern matches dlt, Prefect, dbt — all use named-backend resolution, not direct imports.
ADR at `docs/adr/020-registry-fallback-named-backend.md`.
Tests at `tests/test_registry_fallback.py` (5 tests including a static source scan that
asserts no `from infrastructure` lines remain in `application/`).

## Audit Fixes (phases 12-16 bugs)

- `domain/job_group.py::remove_job_from_group`: was filtering by `raw_item_id`, should filter by `stable_id` (data corruption fix).
- `application/search_text.py`: `str(WorkMode.UNKNOWN) != "UNKNOWN"` always True — fixed to `job.work_mode != WorkMode.UNKNOWN`.
- `infrastructure/backends/search/hybrid.py`: added `close()` to propagate cleanup.
- `infrastructure/backends/vector/qdrant.py`: `search()` → `query_points()` (deprecated API), `hashlib.md5(usedforsecurity=False)`.

## Verification

- `mypy`: 0 errors, 93 source files
- `ruff check`: passed
- `bandit`: 0 HIGH/MEDIUM/LOW
- `pytest`: 180 passed, 2 skipped (postgres integration, expected)
- Layer boundary: `application/` no longer imports `infrastructure/`
