# Roadmap

## Goal

Build `job_ftch` as a lean MVP pipeline that reaches real-world inputs early, keeps the
core architecture stable, and grows source coverage and output quality incrementally.

## Delivery rules

- Real-world contact as early as possible: Telegram channels, Telegram groups/comments,
  and career sites should enter the pipeline early.
- After the core spine, work should split cleanly across 3 parallel streams:
  Telegram, career sites, and cross-cutting pipeline quality.
- Every task should end in working code, tests, and an observable result.
- Avoid speculative infra until the MVP loop is proven.

## Phase 0. Spine

Purpose: create the smallest correct executable core.

### RM-001 Core domain models
- Add `RawItem`, `Job`, and supporting value objects/enums.
- Define stable IDs, serialization, and domain invariants.
- Add unit tests for valid and invalid payloads.

### RM-002 Ports and contracts
- Define `Source`, `Node`, `Sink`, `Store`, and `LLMProvider` protocols.
- Keep `domain/` clean from infrastructure imports.
- Add contract-oriented tests for minimal implementations.

### RM-003 Pipeline engine v1
- Implement async source -> nodes -> sink orchestration.
- Support item drop / pass-through semantics.
- Add a pipeline happy-path integration test.

### RM-004 Config and composition root
- Replace the `app.py` placeholder with a real composition root.
- Harden `Settings` loading from `.env`.
- Add a minimal runnable local pipeline command.

### RM-005 In-memory store
- Implement `InMemoryStore` for processed IDs, dedup keys, and simple run state.
- Add tests for idempotent reads/writes.

### RM-006 Local debug source and JSON sink
- Add a fixture-based local source.
- Add `JsonFileSink` with predictable JSON or JSONL output.
- Prove that the pipeline can run locally with no external services.

### RM-007 Minimal run observability
- Add structured logging and a run summary.
- Log counts for fetched, dropped, emitted, and failed items.

## Phase 1. First real-world ingestion

Purpose: connect the pipeline to actual external data as early as possible.

### RM-008 Source normalization boundary
- Define one canonical `RawItem` shape across all sources.
- Standardize source kind, external ID, URL, timestamps, text, and metadata.

### RM-009 Telegram channel source v1
- Ingest posts from Telegram channels in read-only mode.
- Map raw messages into normalized `RawItem`.
- Add integration tests with recorded samples where possible.

### RM-010 Telegram group source v1
- Ingest posts/messages from Telegram groups in read-only mode.
- Normalize sender/chat/message metadata into `RawItem`.

### RM-011 Telegram comments source v1
- Ingest comments to channel posts as a separate source type.
- Keep comment lineage in metadata for later filtering/dedup.

### RM-012 Career site source v1
- Implement one simple career-site adapter with `httpx + selectolax`.
- Normalize HTML-derived items into `RawItem`.

### RM-013 Real sample fixtures
- Store sanitized real-world fixtures for channels, groups, comments, and career pages.
- Use them in regression tests for source adapters.

## Phase 2. Input hygiene and protection

Purpose: defend the pipeline from noisy or malformed raw input before expensive work.

### RM-014 SanitizeNode v1
- Normalize whitespace, control characters, broken encoding, and URL formatting.
- Guarantee that `SanitizeNode` is first in every pipeline chain.

### RM-015 Raw input validation
- Reject malformed `RawItem` objects and missing required fields.
- Add explicit rejection reasons.

### RM-016 URL and origin policy
- Validate external URLs and allowed career-site domains.
- Reject suspicious or malformed origins.

### RM-017 Quarantine flow
- Route malformed or suspicious raw items to a quarantine sink/log.
- Keep them observable instead of silently dropping them.

## Phase 3. Early signal shaping

Purpose: cheaply separate likely-job signal from obvious noise.

### RM-018 Heuristic triage node
- Drop empty, too-short, and obviously irrelevant posts before extraction.
- Add stable rejection reason taxonomy.

### RM-019 Telegram-specific heuristics
- Add rules for vacancy-like Telegram messages.
- Tune for channels, groups, and comments separately.

### RM-020 Career-site heuristics
- Drop navigation pages, generic company pages, and non-job content.

### RM-021 Stage conversion reporting
- Report conversion across raw -> sanitized -> triaged.
- Make source quality visible by source type.

## Phase 4. Dedup and identity

Purpose: stabilize reruns and avoid inflated job counts.

### RM-022 Raw item identity
- Define processed keys per source/external ID/URL.
- Make reruns idempotent at raw-item level.

### RM-023 Job dedup v1
- Dedup by canonical URL and normalized text/title/company signals.

### RM-024 Near-duplicate detection
- Add fuzzy dedup with `rapidfuzz` for reposts and edited duplicates.

### RM-025 Cross-source dedup
- Detect duplicates across Telegram and career-site sources.

### RM-026 Dedup explainability
- Persist why an item/job was marked as duplicate.

## Phase 5. Extraction and schema quality

Purpose: turn filtered raw signal into structured jobs.

### RM-027 Job schema v1 finalization
- Finalize the practical MVP `Job` schema.
- Distinguish required vs optional fields.

### RM-028 Extraction node v1
- Convert `RawItem` into structured `Job` output.
- Handle parse success, partial success, and failure explicitly.

### RM-029 LLM provider adapter
- Implement `openai + instructor` behind `LLMProvider`.
- Add timeout, retry, and schema validation behavior.

### RM-030 Extraction validation layer
- Validate extracted jobs after parsing.
- Reject or downgrade low-quality results.

### RM-031 Partial extraction strategy
- Preserve useful partial jobs instead of losing the full item.

### RM-032 Gold sample evaluation set
- Maintain a real-world extraction sample set for regression and manual scoring.

## Phase 6. Job quality and relevance

Purpose: make output useful for the target AI-jobs niche.

### RM-033 Job validation node
- Enforce minimum viable usefulness for emitted jobs.

### RM-034 AI-role relevance filter
- Keep jobs in the target niche: LLM, AI PM, MLOps, AgentOps, AI Infra, related roles.

### RM-035 Title and company normalization
- Clean noisy company/title strings after extraction.

### RM-036 Location and work-mode normalization
- Normalize remote, hybrid, on-site, and free-form location text.

### RM-037 Compensation parsing v1
- Parse salary and compensation details when present.

### RM-038 Quality scoring
- Add a score or confidence indicator for downstream use and review.

## Phase 7. Outputs and feedback loop

Purpose: make results consumable by operators and downstream users.

### RM-039 JSON sink hardening
- Support atomic writes, stable schema versioning, and JSONL mode.

### RM-040 Rejected-items sink
- Write dropped, quarantined, and failed items to a separate sink.

### RM-041 Posting sink v1
- Publish selected jobs to Telegram or another outbound target.

### RM-042 Human review output
- Export borderline jobs for quick manual review.

### RM-043 CLI run modes
- Add `once`, source selection, output path, item limit, and dry-run modes.

### RM-044 Run summary report
- Summarize per-source counts, duplicates, failures, extracted jobs, and posted jobs.

## Phase 8. Reliability and failure isolation

Purpose: survive bad items and partial infrastructure failures.

### RM-045 Per-item fault isolation
- Keep one bad item from failing the whole run.

### RM-046 Source retry and timeout policy
- Add retry/timeout rules for Telegram and HTTP sources.

### RM-047 LLM resilience rules
- Add bounded retries, timeout control, and safe failure behavior for LLM extraction.

### RM-048 Sink recovery behavior
- Define behavior for file-write and posting failures.

### RM-049 Resume and rerun semantics
- Make reruns predictable using store state.

### RM-050 Operational guards
- Add max items, max text length, and source-specific resource limits.

## Phase 9. Test and regression system

Purpose: keep velocity without breaking pipeline quality.

### RM-051 Domain and node unit test pack
- Cover core nodes and domain invariants thoroughly.

### RM-052 Pipeline slice integration tests
- Add end-to-end tests for channels, groups/comments, and career-site slices.

### RM-053 Real-world regression fixtures
- Preserve representative source inputs and expected outcomes.

### RM-054 Dedup regression pack
- Lock down hard duplicate and near-duplicate cases.

### RM-055 Extraction evaluation harness
- Add a repeatable offline quality check against the gold sample set.

### RM-056 Port contract tests
- Ensure new source/node/sink adapters respect shared contracts.

## Phase 10. MVP release contour

Purpose: make the project usable by someone other than the author.

### RM-057 Env examples and config docs
- Provide clean `.env` examples and required variable documentation.

### RM-058 Runnable README flow
- Document setup from clone to first emitted JSON jobs.

### RM-059 Sample outputs and examples
- Add sample job output and source configuration examples.

### RM-060 Source setup guides
- Document Telegram channel/group/comment setup and career-site configuration.

### RM-061 Troubleshooting guide
- Document common Telegram auth, parsing, and extraction failure modes.

### RM-062 Release checklist
- Define the final MVP verification flow and publication checklist.

## Parallel work streams after Phase 0

### Stream A. Telegram
- Channels, groups, comments, Telegram-specific heuristics, Telegram posting.

### Stream B. Career sites
- Career-site adapters, HTML normalization, source-specific heuristics.

### Stream C. Cross-cutting quality
- Sanitize, validation, dedup, extraction, scoring, sinks, resilience, tests.

## Suggested milestone boundaries

- `M1 - Spine`
- `M2 - Real-world ingestion`
- `M3 - Input hygiene and triage`
- `M4 - Dedup and identity`
- `M5 - Extraction`
- `M6 - Job quality`
- `M7 - Outputs and feedback`
- `M8 - Reliability`
- `M9 - Test and regression`
- `M10 - MVP release contour`

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
- Support `--limit <N>` and `--source-kind` filters.

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

