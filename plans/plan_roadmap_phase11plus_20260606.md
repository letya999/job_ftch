# Plan: Extend roadmap.md with Phases 11–15

## Goal
Append five new phases to `docs/roadmap.md` after Phase 10.
These phases extend the existing hexagonal architecture toward a production-capable
multi-source aggregation service with configurable filtering and a search layer.
No existing phases or text should be modified. Only append.

## File to modify
`docs/roadmap.md` — append after the last line ("### Stream C. Cross-cutting quality" section and "Suggested milestone boundaries").

## What to append

Append the following content verbatim (as Markdown) at the very end of `docs/roadmap.md`:

---

## Phase 11. Multi-source orchestration

Purpose: run the pipeline over many sources in a single invocation instead of one source per CLI call.

### RM-063 Source registry in config
- Add `sources: list[SourceConfig]` to `Settings` (replacing the single-source fields).
- `SourceConfig` is a Pydantic discriminated union: `TelegramChannelConfig | TelegramGroupConfig | TelegramCommentConfig | CareerSiteConfig`.
- Keep the old single-source flags as a deprecated compatibility shim during transition.

### RM-064 CompositeSource adapter
- Implement `CompositeSource(sources: Sequence[Source[RawItem]])` in `infrastructure/sources/composite.py`.
- Implements the `Source` protocol: `fetch()` yields items from all child sources sequentially.
- Attach `source_name` / `source_kind` metadata on each item so per-source stats remain accurate.
- Add unit tests: correct ordering, quarantined items from one child do not abort others.

### RM-065 Parallel source fetching
- Extend `CompositeSource` with an optional `concurrency: int` parameter.
- When `concurrency > 1`, fan out with `asyncio.TaskGroup`; merge yielded items via an async queue.
- Preserve per-source error isolation: one failing source increments `failed` but does not stop others.
- Add tests for concurrent fan-out and error isolation.

### RM-066 Per-source run state namespacing
- Namespace store keys by source identity: `{source_kind}:{source_name}:{key}`.
- Ensures 70 sources do not collide in the same store.
- Update `InMemoryStore` and future `SQLiteStore` to use namespaced keys.

### RM-067 Source registry CLI integration
- `app.py` reads `Settings.sources` and builds a `CompositeSource` when multiple sources are configured.
- `--source-backend` / `--telegram-entity` CLI flags remain for single-source quick runs.
- Add `--sources-file` CLI flag that accepts a YAML/JSON file with a list of source configs.

## Phase 12. Persistent store

Purpose: survive restarts, accumulate dedup history, and support reruns across sessions.

### RM-068 SQLiteStore implementation
- Implement `SQLiteStore` in `infrastructure/stores/sqlite.py` implementing the `Store` protocol.
- Schema: three tables — `processed_items(item_id TEXT PK, processed_at TEXT)`, `dedup_keys(key TEXT PK, remembered_at TEXT)`, `run_state(key TEXT PK, value TEXT, updated_at TEXT)`.
- Use `aiosqlite` for async I/O; `CREATE TABLE IF NOT EXISTS` on startup (no migration framework needed at this scale).
- Add `StoreBackend.SQLITE` to `config.py` and wire it in `app.py`.

### RM-069 Store migration path
- On first run with `SQLiteStore`, detect empty DB and log a clear startup message.
- Add a `--reset-store` CLI flag that drops and recreates tables (explicit, not silent).
- Document the store file location in config: `JOB_FTCH_STORE_PATH` env var.

### RM-070 Store health check
- Add `ping() -> bool` to the `Store` protocol.
- Call it during pipeline startup; log a warning and fall back to `InMemoryStore` if unreachable.
- Add a test that `InMemoryStore.ping()` always returns `True`.

### RM-071 Store-backed idempotency regression tests
- Add a regression test: run the same fixture twice with `SQLiteStore`; second run emits 0 items.
- Verify dedup keys are persisted across instantiations.

## Phase 13. Configurable filter profiles

Purpose: let operators tune signal filtering without touching code.

### RM-072 FilterProfile domain model
- Add `FilterProfile` Pydantic model to `domain/models.py`:
  - `required_keywords: list[str]` — item must contain at least one (OR logic).
  - `exclude_keywords: list[str]` — item is dropped if any match.
  - `allowed_source_kinds: list[SourceKind] | None` — None means all.
  - `min_text_tokens: int` — override triage threshold.
  - `min_text_chars: int` — override triage threshold.
- Keep `FilterProfile` in `domain/` (pure Pydantic, no I/O).

### RM-073 HeuristicTriageNode accepts FilterProfile
- Refactor `HeuristicTriageNode.__init__` to accept an optional `FilterProfile`.
- When a profile is provided, its `required_keywords` and `exclude_keywords` override / extend the built-in pattern lists.
- `allowed_source_kinds` gates which source kinds are processed by this node.
- Backward-compatible: `FilterProfile=None` keeps current hardcoded behavior.

### RM-074 Profile loading from config
- Add `filter_profile_path: Path | None` to `Settings`.
- Load profile from a YAML or JSON file at startup; validate with `FilterProfile.model_validate`.
- Document the profile file format in a fixture example at `fixtures/example_filter_profile.yaml`.

### RM-075 Profile-aware stage conversion reporting
- Extend `RunSummary` with `applied_profile: str | None` (profile filename or "default").
- Include in the run summary log line so filtering decisions are traceable.

### RM-076 Profile regression tests
- Add tests: custom `required_keywords` causes correct items to pass / be dropped.
- Add test: `exclude_keywords` overrides a positive `required_keywords` match.

## Phase 14. Fulltext and semantic search layer

Purpose: make collected jobs queryable without exporting raw JSON files.

### RM-077 Job persistence sink
- Add `SQLiteJobSink` in `sinks/sqlite_job.py` implementing `Sink[Job]`.
- Schema: `jobs(stable_id TEXT PK, raw_item_id TEXT, source_kind TEXT, source_name TEXT, title TEXT, company TEXT, description TEXT, location TEXT, work_mode TEXT, canonical_url TEXT, compensation_json TEXT, metadata_json TEXT, emitted_at TEXT)`.
- Use `INSERT OR IGNORE` for idempotent writes.
- Wire as `SinkBackend.SQLITE_JOB` in config.

### RM-078 SQLite FTS5 fulltext index
- Add a `jobs_fts` virtual table (FTS5) on `title || ' ' || company || ' ' || description`.
- Populated via trigger on `jobs` insert.
- No extra dependencies — SQLite ships FTS5.
- Add a `search_jobs(query: str, limit: int) -> list[Job]` function in `infrastructure/stores/sqlite.py`.

### RM-079 CLI search command
- Add `--search "query"` mode to `app.py` that queries `SQLiteJobSink` FTS5 and prints results as JSON.
- Support `--limit N` and `--source-kind` filters.

### RM-080 Embedding sink (optional, gated)
- Add `EmbeddingBackend` enum to config: `NONE | OPENAI | LOCAL`.
- When enabled, compute embeddings for `Job.title + Job.description` after extraction and store as `BLOB` in a `job_embeddings(stable_id TEXT PK, embedding BLOB, model TEXT)` table.
- Wire to `LLMProvider` protocol so embedding backend is swappable.
- Default: `NONE` — zero cost for users who do not need semantic search.

### RM-081 Semantic search command
- When `EmbeddingBackend != NONE`, add `--semantic-search "query"` CLI flag.
- Compute query embedding, rank by cosine similarity using `numpy` (no vector DB dependency).
- Return top-K results merged with FTS5 results (union, deduplicated by `stable_id`).

### RM-082 Search result export
- Add `--search-output path.json` to write search results as JSON (same schema as pipeline output).
- Enables downstream use without re-running the pipeline.

## Phase 15. Scheduler and daemon mode

Purpose: run the pipeline continuously over many sources without manual CLI invocations.

### RM-083 Run interval config
- Add `schedule_interval_seconds: int | None` to `Settings` (default: None = single run).
- Per-source override: add `interval_seconds: int | None` to `SourceConfig`.

### RM-084 Asyncio scheduler
- Implement `Scheduler` in `application/scheduler.py`.
- Uses `asyncio` event loop — no Celery, no APScheduler, no cron.
- Runs each source group at its configured interval using `asyncio.sleep` between runs.
- Respects shutdown signals: `SIGINT` / `SIGTERM` → drain in-flight runs → exit cleanly.

### RM-085 Daemon CLI mode
- Add `--daemon` flag to `app.py`; when set, starts the `Scheduler` instead of a single `Pipeline.run`.
- Log scheduler start, next run times, and per-source outcomes per tick.
- Write PID file to `JOB_FTCH_RUN_DIR` (configurable) for process management.

### RM-086 Rate-limit and backoff policy
- Add per-source `rate_limit: RateLimitConfig` to `SourceConfig`:
  - `min_interval_seconds` — minimum time between two fetches of the same source.
  - `backoff_multiplier` — exponential backoff on repeated failures.
- `Scheduler` enforces this before dispatching a source run.
- Prevents Telegram flood-wait and HTTP 429 in unattended runs.

### RM-087 Scheduler observability
- Extend `RunSummary` with `scheduled_run_index: int` and `source_id: str` for scheduler context.
- Log scheduler tick summary: sources run, items fetched, items emitted, next tick time.
- Expose a `--status` CLI flag that reads the last run summary from the store and prints it.

### RM-088 Scheduler regression and load tests
- Add tests: scheduler runs two ticks; second tick skips already-processed items.
- Add test: source failure in one tick does not prevent next tick.
- Add test: `--daemon` with `--max-items` stops after limit across all sources.

## Updated milestone boundaries

- `M11 - Multi-source` — RM-063 to RM-067
- `M12 - Persistent store` — RM-068 to RM-071
- `M13 - Configurable filters` — RM-072 to RM-076
- `M14 - Search layer` — RM-077 to RM-082
- `M15 - Daemon mode` — RM-083 to RM-088

---

## Implementation notes for the agent

- Open `docs/roadmap.md`.
- Do NOT modify any existing content.
- Append the block above (starting from `## Phase 11.`) at the very end of the file.
- Preserve existing Markdown formatting style (## for phase headers, ### for task headers).
- No trailing whitespace, one blank line between sections.
- After appending, verify the file ends cleanly with a newline.
