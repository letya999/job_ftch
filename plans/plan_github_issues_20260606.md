# Plan: Create GitHub Issues for all Roadmap Tasks (RM-001 to RM-088)

## Goal
Create GitHub issues for every task in `docs/roadmap.md` on repo `letya999/job_ftch`.
Use `gh issue create` for each issue. First create labels, then issues.

## Step 1: Create Labels
Run these gh label create commands (skip if label already exists using `gh label list` first):

Labels to create:
- `phase-0` color `#0075ca` description "Phase 0: Spine"
- `phase-1` color `#0075ca` description "Phase 1: First real-world ingestion"
- `phase-2` color `#0075ca` description "Phase 2: Input hygiene and protection"
- `phase-3` color `#0075ca` description "Phase 3: Early signal shaping"
- `phase-4` color `#e4e669` description "Phase 4: Dedup and identity"
- `phase-5` color `#e4e669` description "Phase 5: Extraction and schema quality"
- `phase-6` color `#e4e669` description "Phase 6: Job quality and relevance"
- `phase-7` color `#d93f0b` description "Phase 7: Outputs and feedback loop"
- `phase-8` color `#d93f0b` description "Phase 8: Reliability and failure isolation"
- `phase-9` color `#d93f0b` description "Phase 9: Test and regression system"
- `phase-10` color `#0e8a16` description "Phase 10: MVP release contour"
- `phase-11` color `#5319e7` description "Phase 11: Multi-source orchestration"
- `phase-12` color `#5319e7` description "Phase 12: Persistent store"
- `phase-13` color `#5319e7` description "Phase 13: Configurable filter profiles"
- `phase-14` color `#5319e7` description "Phase 14: Fulltext and semantic search layer"
- `phase-15` color `#5319e7` description "Phase 15: Scheduler and daemon mode"

## Step 2: Create Issues

Use `gh issue create --repo letya999/job_ftch --title "..." --body "..." --label "phase-N"` for each.

### Phase 0 — phase-0

RM-001: Core domain models
Body: Add `RawItem`, `Job`, and supporting value objects/enums. Define stable IDs, serialization, and domain invariants. Add unit tests for valid and invalid payloads.

RM-002: Ports and contracts
Body: Define `Source`, `Node`, `Sink`, `Store`, and `LLMProvider` protocols. Keep `domain/` clean from infrastructure imports. Add contract-oriented tests for minimal implementations.

RM-003: Pipeline engine v1
Body: Implement async source -> nodes -> sink orchestration. Support item drop / pass-through semantics. Add a pipeline happy-path integration test.

RM-004: Config and composition root
Body: Replace the `app.py` placeholder with a real composition root. Harden `Settings` loading from `.env`. Add a minimal runnable local pipeline command.

RM-005: In-memory store
Body: Implement `InMemoryStore` for processed IDs, dedup keys, and simple run state. Add tests for idempotent reads/writes.

RM-006: Local debug source and JSON sink
Body: Add a fixture-based local source. Add `JsonFileSink` with predictable JSON or JSONL output. Prove that the pipeline can run locally with no external services.

RM-007: Minimal run observability
Body: Add structured logging and a run summary. Log counts for fetched, dropped, emitted, and failed items.

### Phase 1 — phase-1

RM-008: Source normalization boundary
Body: Define one canonical `RawItem` shape across all sources. Standardize source kind, external ID, URL, timestamps, text, and metadata.

RM-009: Telegram channel source v1
Body: Ingest posts from Telegram channels in read-only mode. Map raw messages into normalized `RawItem`. Add integration tests with recorded samples where possible.

RM-010: Telegram group source v1
Body: Ingest posts/messages from Telegram groups in read-only mode. Normalize sender/chat/message metadata into `RawItem`.

RM-011: Telegram comments source v1
Body: Ingest comments to channel posts as a separate source type. Keep comment lineage in metadata for later filtering/dedup.

RM-012: Career site source v1
Body: Implement one simple career-site adapter with `httpx + selectolax`. Normalize HTML-derived items into `RawItem`.

RM-013: Real sample fixtures
Body: Store sanitized real-world fixtures for channels, groups, comments, and career pages. Use them in regression tests for source adapters.

### Phase 2 — phase-2

RM-014: SanitizeNode v1
Body: Normalize whitespace, control characters, broken encoding, and URL formatting. Guarantee that `SanitizeNode` is first in every pipeline chain.

RM-015: Raw input validation
Body: Reject malformed `RawItem` objects and missing required fields. Add explicit rejection reasons.

RM-016: URL and origin policy
Body: Validate external URLs and allowed career-site domains. Reject suspicious or malformed origins.

RM-017: Quarantine flow
Body: Route malformed or suspicious raw items to a quarantine sink/log. Keep them observable instead of silently dropping them.

### Phase 3 — phase-3

RM-018: Heuristic triage node
Body: Drop empty, too-short, and obviously irrelevant posts before extraction. Add stable rejection reason taxonomy.

RM-019: Telegram-specific heuristics
Body: Add rules for vacancy-like Telegram messages. Tune for channels, groups, and comments separately.

RM-020: Career-site heuristics
Body: Drop navigation pages, generic company pages, and non-job content.

RM-021: Stage conversion reporting
Body: Report conversion across raw -> sanitized -> triaged. Make source quality visible by source type.

### Phase 4 — phase-4

RM-022: Raw item identity
Body: Define processed keys per source/external ID/URL. Make reruns idempotent at raw-item level.

RM-023: Job dedup v1
Body: Dedup by canonical URL and normalized text/title/company signals.

RM-024: Near-duplicate detection
Body: Add fuzzy dedup with `rapidfuzz` for reposts and edited duplicates.

RM-025: Cross-source dedup
Body: Detect duplicates across Telegram and career-site sources.

RM-026: Dedup explainability
Body: Persist why an item/job was marked as duplicate.

### Phase 5 — phase-5

RM-027: Job schema v1 finalization
Body: Finalize the practical MVP `Job` schema. Distinguish required vs optional fields.

RM-028: Extraction node v1
Body: Convert `RawItem` into structured `Job` output. Handle parse success, partial success, and failure explicitly.

RM-029: LLM provider adapter
Body: Implement `openai + instructor` behind `LLMProvider`. Add timeout, retry, and schema validation behavior.

RM-030: Extraction validation layer
Body: Validate extracted jobs after parsing. Reject or downgrade low-quality results.

RM-031: Partial extraction strategy
Body: Preserve useful partial jobs instead of losing the full item.

RM-032: Gold sample evaluation set
Body: Maintain a real-world extraction sample set for regression and manual scoring.

### Phase 6 — phase-6

RM-033: Job validation node
Body: Enforce minimum viable usefulness for emitted jobs.

RM-034: AI-role relevance filter
Body: Keep jobs in the target niche: LLM, AI PM, MLOps, AgentOps, AI Infra, related roles.

RM-035: Title and company normalization
Body: Clean noisy company/title strings after extraction.

RM-036: Location and work-mode normalization
Body: Normalize remote, hybrid, on-site, and free-form location text.

RM-037: Compensation parsing v1
Body: Parse salary and compensation details when present.

RM-038: Quality scoring
Body: Add a score or confidence indicator for downstream use and review.

### Phase 7 — phase-7

RM-039: JSON sink hardening
Body: Support atomic writes, stable schema versioning, and JSONL mode.

RM-040: Rejected-items sink
Body: Write dropped, quarantined, and failed items to a separate sink.

RM-041: Posting sink v1
Body: Publish selected jobs to Telegram or another outbound target.

RM-042: Human review output
Body: Export borderline jobs for quick manual review.

RM-043: CLI run modes
Body: Add `once`, source selection, output path, item limit, and dry-run modes.

RM-044: Run summary report
Body: Summarize per-source counts, duplicates, failures, extracted jobs, and posted jobs.

### Phase 8 — phase-8

RM-045: Per-item fault isolation
Body: Keep one bad item from failing the whole run.

RM-046: Source retry and timeout policy
Body: Add retry/timeout rules for Telegram and HTTP sources.

RM-047: LLM resilience rules
Body: Add bounded retries, timeout control, and safe failure behavior for LLM extraction.

RM-048: Sink recovery behavior
Body: Define behavior for file-write and posting failures.

RM-049: Resume and rerun semantics
Body: Make reruns predictable using store state.

RM-050: Operational guards
Body: Add max items, max text length, and source-specific resource limits.

### Phase 9 — phase-9

RM-051: Domain and node unit test pack
Body: Cover core nodes and domain invariants thoroughly.

RM-052: Pipeline slice integration tests
Body: Add end-to-end tests for channels, groups/comments, and career-site slices.

RM-053: Real-world regression fixtures
Body: Preserve representative source inputs and expected outcomes.

RM-054: Dedup regression pack
Body: Lock down hard duplicate and near-duplicate cases.

RM-055: Extraction evaluation harness
Body: Add a repeatable offline quality check against the gold sample set.

RM-056: Port contract tests
Body: Ensure new source/node/sink adapters respect shared contracts.

### Phase 10 — phase-10

RM-057: Env examples and config docs
Body: Provide clean `.env` examples and required variable documentation.

RM-058: Runnable README flow
Body: Document setup from clone to first emitted JSON jobs.

RM-059: Sample outputs and examples
Body: Add sample job output and source configuration examples.

RM-060: Source setup guides
Body: Document Telegram channel/group/comment setup and career-site configuration.

RM-061: Troubleshooting guide
Body: Document common Telegram auth, parsing, and extraction failure modes.

RM-062: Release checklist
Body: Define the final MVP verification flow and publication checklist.

### Phase 11 — phase-11

RM-063: Source registry in config
Body: Add `sources: list[SourceConfig]` to `Settings` as a Pydantic discriminated union. Keep old single-source flags as deprecated compatibility shim.

RM-064: CompositeSource adapter
Body: Implement `CompositeSource` in `infrastructure/sources/composite.py` implementing the `Source` protocol. Yields items from all child sources sequentially. Add unit tests for ordering and quarantine isolation.

RM-065: Parallel source fetching
Body: Extend `CompositeSource` with optional `concurrency: int`. Fan out with `asyncio.TaskGroup`, merge via async queue. Per-source error isolation: one failing source does not stop others.

RM-066: Per-source run state namespacing
Body: Namespace store keys by `{source_kind}:{source_name}:{key}` to prevent 70-source key collisions. Update `InMemoryStore` and future `SQLiteStore`.

RM-067: Source registry CLI integration
Body: `app.py` builds `CompositeSource` from `Settings.sources`. Add `--sources-file` CLI flag accepting YAML/JSON source list. Keep `--source-backend`/`--telegram-entity` for single-source quick runs.

### Phase 12 — phase-12

RM-068: SQLiteStore implementation
Body: Implement `SQLiteStore` in `infrastructure/stores/sqlite.py` with `aiosqlite`. Tables: `processed_items`, `dedup_keys`, `run_state`. `CREATE TABLE IF NOT EXISTS` on startup. Wire as `StoreBackend.SQLITE`.

RM-069: Store migration path
Body: Detect empty DB on first run and log startup message. Add `--reset-store` CLI flag. Document `JOB_FTCH_STORE_PATH` env var.

RM-070: Store health check
Body: Add `ping() -> bool` to `Store` protocol. Call during pipeline startup; fall back to `InMemoryStore` if unreachable. Test that `InMemoryStore.ping()` always returns `True`.

RM-071: Store-backed idempotency regression tests
Body: Test: run same fixture twice with `SQLiteStore`; second run emits 0 items. Verify dedup keys persist across instantiations.

### Phase 13 — phase-13

RM-072: FilterProfile domain model
Body: Add `FilterProfile` Pydantic model to `domain/models.py`: `required_keywords`, `exclude_keywords`, `allowed_source_kinds`, `min_text_tokens`, `min_text_chars`. Keep in `domain/` — pure Pydantic, no I/O.

RM-073: HeuristicTriageNode accepts FilterProfile
Body: Refactor `HeuristicTriageNode.__init__` to accept optional `FilterProfile`. Profile keywords override/extend built-in lists. Backward-compatible: `None` keeps current behavior.

RM-074: Profile loading from config
Body: Add `filter_profile_path: Path | None` to `Settings`. Load and validate YAML/JSON profile at startup. Add `fixtures/example_filter_profile.yaml`.

RM-075: Profile-aware stage conversion reporting
Body: Extend `RunSummary` with `applied_profile: str | None`. Include in run summary log line.

RM-076: Profile regression tests
Body: Test custom `required_keywords` causes correct pass/drop. Test `exclude_keywords` overrides positive match.

### Phase 14 — phase-14

RM-077: Job persistence sink
Body: Add `SQLiteJobSink` in `sinks/sqlite_job.py` implementing `Sink[Job]`. Full job schema table with `INSERT OR IGNORE`. Wire as `SinkBackend.SQLITE_JOB`.

RM-078: SQLite FTS5 fulltext index
Body: Add `jobs_fts` virtual FTS5 table on title+company+description, populated by trigger. Add `search_jobs(query, limit) -> list[Job]` function. No extra dependencies.

RM-079: CLI search command
Body: Add `--search "query"` mode to `app.py` querying FTS5. Support `--limit N` and `--source-kind` filters.

RM-080: Embedding sink (optional, gated)
Body: Add `EmbeddingBackend` enum: `NONE | OPENAI | LOCAL`. Store embeddings as BLOB in `job_embeddings` table. Default `NONE` — zero cost when unused.

RM-081: Semantic search command
Body: Add `--semantic-search "query"` CLI flag when embeddings enabled. Compute query embedding, rank by cosine similarity with `numpy`. Merge with FTS5 results, deduplicate by `stable_id`.

RM-082: Search result export
Body: Add `--search-output path.json` to write search results as JSON in standard job schema.

### Phase 15 — phase-15

RM-083: Run interval config
Body: Add `schedule_interval_seconds: int | None` to `Settings` (default None = single run). Per-source `interval_seconds` override in `SourceConfig`.

RM-084: Asyncio scheduler
Body: Implement `Scheduler` in `application/scheduler.py` using pure `asyncio`. Runs source groups at configured intervals. Respects `SIGINT`/`SIGTERM` for graceful shutdown.

RM-085: Daemon CLI mode
Body: Add `--daemon` flag to `app.py` that starts `Scheduler` instead of single `Pipeline.run`. Log next run times. Write PID file to `JOB_FTCH_RUN_DIR`.

RM-086: Rate-limit and backoff policy
Body: Add per-source `rate_limit: RateLimitConfig` with `min_interval_seconds` and `backoff_multiplier`. `Scheduler` enforces limits before dispatch. Prevents Telegram flood-wait and HTTP 429.

RM-087: Scheduler observability
Body: Extend `RunSummary` with `scheduled_run_index` and `source_id`. Log scheduler tick summary. Add `--status` CLI flag showing last run summary from store.

RM-088: Scheduler regression and load tests
Body: Test: two scheduler ticks, second skips processed items. Test: source failure in one tick does not block next. Test: `--daemon --max-items` stops after limit across all sources.

## Execution instructions for the agent

1. Run `gh label list --repo letya999/job_ftch` to see existing labels.
2. Create missing phase labels using `gh label create` (skip existing ones, do not error).
3. For each issue above, run:
   `gh issue create --repo letya999/job_ftch --title "[RM-XXX] Title" --body "Body text" --label "phase-N"`
4. Create all 88 issues in order RM-001 to RM-088.
5. After completing, run `gh issue list --repo letya999/job_ftch --limit 100` to verify count.
6. Report total issues created and any errors.
