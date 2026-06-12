# Roadmap

> Historical note (2026-06-12): this file is the master implementation plan and still keeps
> original phase/task names where they describe the rollout as planned at that time.
> The live architecture and canonical payload contract are tracked in
> `docs/architecture.md` and the accepted ADRs.

> Status snapshot (2026-06-12): the current worktree and test suite confirm milestone
> coverage through M25 (`280 passed, 8 skipped`). M26 is partially landed
> (`JobLineage`, tenant/MCP lineage surface, source run ids, persisted run history,
> `IncrementalCursor` used by RSS and REST API sources, `PrometheusExporter` behind
> `metrics_enabled/metrics_port`), while broader cursor migration and deeper
> observability hardening remain roadmap work. M27 stays the target
> platform shape, not a claim that every listed subsystem is already shipped.

## Goal

Build `job_ftch` as a **library-first**, domain-specific data ingestion engine for job postings.
The library ingests from heterogeneous sources (Telegram, career sites, APIs, feeds, webhooks),
moves data through the canonical payload family `RawItem -> JobDraft -> JobRecord -> JobGroup`,
and emits normalized records to pluggable sinks — without coupling to any runtime orchestrator.
Any wrapper (CLI, FastStream, FastAPI, Dagster, Airflow, MCP server) is an adapter on top,
not part of the core.

## Delivery rules

- Real-world contact as early as possible: Telegram channels, groups/comments, and career sites
  should enter the pipeline early.
- After the core spine, work splits cleanly across 3 parallel streams:
  Telegram, career sites, and cross-cutting pipeline quality.
- Every task ends in working code, tests, and an observable result.
- Avoid speculative infra until the MVP loop is proven.
- Library-first: `app.py` is one runner. Core never imports CLI, FastStream, or Dagster.

## Architecture principles

### Module boundaries — HARD RULES, no exceptions
- `domain/` imports only pydantic + stdlib. Zero infra deps, ever.
- `application/` imports only `domain/` + stdlib + pydantic. No sqlalchemy, asyncpg, httpx,
  playwright, fastapi, faststream, telegram, qdrant, openai.
- `nodes/` and `sinks/` import only `domain/` and `application/`. No infra clients.
- Everything from Phase 15 onward (bypass, browser, vector DB, MCP, bot, Dagster) lives
  in `infrastructure/`, `adapters/`, or a separate package. Core never imports it.
- Violation check: `grep -r "from infrastructure" domain/ application/ nodes/ sinks/`
  must return empty. Enforced in CI.

### Namespace — HARD RULE
- All importable code lives under the `job_ftch` package. No top-level `domain`, `nodes`,
  `sinks`, `application` modules in the installed wheel. Top-level names like `import nodes`
  or `import sinks` pollute every consumer's namespace and collide with other libraries.
- Target layout: `job_ftch/domain/`, `job_ftch/application/`, `job_ftch/nodes/`,
  `job_ftch/sinks/`, `job_ftch/infrastructure/`. All internal imports updated accordingly.
- RM-110 (namespace restructure) is a BLOCKER for any library release. Pull it EARLY —
  see "Execution plan → Do RM-110 early". It ships in the v1.0 line, not at the end;
  the longer it waits, the more modules have to move.

### Extension points
- New source, sink, store, parser, bypass, job_backend, search_backend, embedding_provider,
  or vector_backend arrives via `@register_*(name)` decorator and optional entry point —
  never a core edit.
- Factory signatures accept `SourceSpec + AuthProvider`, not monolithic `Settings`.
  `Settings`-based factories remain only as CLI shims (Phase 11, RM-067b).

### Data contracts
- The target public payload family is `RawItem`, `JobDraft`, `JobRecord`, `JobGroup`.
- `JobRecord` carries `schema_version`. Breaking field changes bump the versioned public
  contract. Evolution policy per field: `evolve` (additive, safe) / `freeze` (no changes) /
  `discard` (drop old field with deprecation cycle). See Phase 13 and ADR-024.
- The required raw-to-structured boundary is `RawItem → JobDraft`.
  Downstream rollout may continue through `JobRecord` and `JobGroup` rather than one flat
  `Job` payload. Typed contract tests remain the enforcement mechanism.

### Scope discipline
- Phases 0-14: core pipeline, domain, quality, search, scheduler.
- Domain value (canonical job, lifecycle) is built before storage, search, and adapters so those layers are group-aware from day one and require no rework.
- Phases 15-19: pluggable extension protocols and library packaging. No new domain logic.
- Phases 20-25: operational features (multi-tenant, MCP, bot, aggregation, lifecycle).
- Heavy optional deps (playwright, qdrant-client, sentence-transformers, aiogram) are
  ALWAYS in extras groups, never in core `[project.dependencies]`.
- Adapters (FastStream, Dagster, Airflow, MCP server, Telegram bot) live in `adapters/`
  or separate repositories. Core `job_ftch` package has zero knowledge of them.

### Other invariants
- Source config and credentials are always separate: `SourceSpec` holds config,
  `AuthProvider` resolves secrets. Secrets never live inside `SourceSpec` or YAML files.
- Lightweight default is always zero-infra: InMemory for dev/tests, SQLite for self-hosted persistence. PostgreSQL, Qdrant, pgvector are scale-up options, never required for a working install.
- Bypass/protection strategies are optional plugins, injected at build time.
- Ingestion mode (polling, push, event-listener, RSS, webhook) is orthogonal to parsing.
- See ADR-006, ADR-007, ADR-008, ADR-009, ADR-010 for the hardening baseline.

## Execution plan — release lines, fast path, parallelization, repo split

The phase numbers below describe WHERE work lives, not the order you must build it.
This section is the actual build order. Priority: ship a working bot fast, then harden
into a library, then grow the platform tail. Do not build the platform tail before the bot works.

### Release lines

- **v0.5 — Working bot (FAST PATH, primary goal).** The shortest route to a real,
  continuously-running job bot. Single tenant. Sources: Telegram (done) + declarative HTML
  (done) + optional official APIs. Persistence: SQLite. NO bypass, NO browser, NO multi-tenant,
  NO MCP, NO vector search. This is "a working bot", not a platform.
  Tasks: RM-063..RM-067 (multi-source) → RM-068a (SQLite store) → RM-079a (SQLite job backend
  + FTS5 search) → RM-086..RM-088 (scheduler/daemon) → RM-128..RM-133 (bot, polling mode).
- **v1.0 — Lightweight embeddable library.** Makes `pip install job_ftch` + `import job_ftch`
  real. Tasks: RM-110 (namespace `job_ftch/`) + RM-110a (PipelineBuilder) + RM-110b (boundary
  CI) + extras groups, plus the domain differentiators RM-134/135/136 (schema_version,
  lifecycle, company canonicalization) and RM-137..RM-140 (cross-source aggregation).
  RM-110 is a release BLOCKER and must be done EARLY (see "Do RM-110 early" below).
- **v1.x — Platform tail (optional, mostly parallel, some community/separate-repo).**
  Official API adapters, realtime/push, persistent Postgres, multi-tenant, MCP server,
  observability. None of these gate the working bot or the library release.

### Fast path to a working bot (do these, skip the rest for now)

1. Phase 11 — multi-source (`SourceSpec`, `CompositeSource`, `sources.yaml`). Needed to list
   many channels/sites in one config.
2. RM-068a — `SQLiteStore` (survives restart, zero infra). Needed for any real bot.
3. RM-110 + RM-110a — namespace + `PipelineBuilder` (do this here, before the bot adapter,
   so the bot imports the clean public API and you never re-move modules).
4. RM-079a — `SQLiteJobBackend` + FTS5 (so the bot's `/search` works without Postgres).
5. Phase 17 — scheduler/daemon (RM-086..RM-088) so the bot updates on a timer.
6. Phase 25 — bot itself (RM-128..RM-133), polling mode (no public HTTPS needed).
   Lifecycle (RM-135) is recommended for digest quality but can follow the first bot.
   Cross-source aggregation (Phase 14) makes the bot best-in-class but is a v1.1 upgrade,
   not required for a working bot.

EXPLICITLY DEFERRED until after the bot works: bypass (RM-094/095), browser/hard scraper
(Phase 20), Postgres (RM-068/070), vector/semantic search (RM-082/083), multi-tenant
(Phase 23), MCP server (Phase 24), realtime/webhook/websocket (Phase 21), platform adapters
FastStream/Dagster/FastAPI (RM-111/112/114). Build these only when a real need appears.

### Do RM-110 (namespace) early

The Goal is "library-first", but library packaging sits at Phase 22. Every phase built under
the old flat layout (`domain/`, `nodes/`) makes the eventual move bigger and riskier.
Pull RM-110 + RM-110a forward to the v0.5/v1.0 boundary (step 3 above). After it, all new
code is written under `job_ftch/` from the start. Treat the current flat layout as legacy.

### What can run in PARALLEL

- After Phase 11 (`SourceSpec`) lands, these are independent and parallelizable:
  - SQLite store + job backend (RM-068a, RM-079a) — infra track.
  - Filter profiles + configurable relevance (Phase 12) — domain track.
  - Bot skeleton (Phase 25, commands + formatter) — adapter track, against a stub pipeline.
- Domain hardening (Phase 13) and cross-source aggregation (Phase 14) are parallel with the
  scheduler (Phase 17) — different files, no shared state.
- Official API adapters (RM-099/100/101) are parallel with each other and with the bot.
- The 6 parallel work streams (A–F, below) map cleanly onto separate contributors.
- SEQUENTIAL constraint: RM-110 (namespace) must land before the bot adapter and before any
  platform adapter, or imports get rewritten twice.

### Repo split policy

- **Core repo (`job_ftch`)** — domain, application, nodes, sinks, and core infrastructure:
  Telegram + declarative HTML + official API sources, InMemory/SQLite/Postgres stores,
  JSON/posting sinks. This is the lightweight installable library. Keep it small.
- **Separate repo / `adapters/` from the start** — anything that wraps an external runtime:
  Telegram bot + FastAPI bridge (Phase 25), FastStream/Dagster/FastAPI adapters (RM-111/112/114),
  MCP server (Phase 24, RM-121..RM-127). Core has zero knowledge of these.
- **Extract to a separate community repo LATER** — browser + hard scraper + bypass
  (Phase 20 and RM-094/095): they are a scraping arms race and must not gate core releases.

### COMMUNITY-MAINTAINED (best-effort, does NOT gate core releases)

- Bypass strategies: `StealthBrowserBypass`, `CaptchaSolverBypass`, `ProxyRotatorBypass`,
  `BehaviorSimBypass`, `ManagedScraperBypass` (RM-094, RM-095, RM-103, RM-105).
- Browser and hard scraper sources (Phase 20, RM-102/104).
- Rationale: CAPTCHA/stealth/anti-bot techniques rot in weeks. Marking them community-maintained
  and moving them to a separate repo keeps the core lightweight and its release cadence free.
- Recommended production answer for protected sites is `ManagedScraperBypass` (a paid API),
  not a self-maintained browser farm.

## Phase 0. Spine — DONE

Purpose: create the smallest correct executable core.

### RM-001 Core domain models
### RM-002 Ports and contracts
### RM-003 Pipeline engine v1
### RM-004 Config and composition root
### RM-005 In-memory store
### RM-006 Local debug source and JSON sink
### RM-007 Minimal run observability

## Phase 1. First real-world ingestion — DONE

Purpose: connect the pipeline to actual external data as early as possible.

### RM-008 Source normalization boundary
### RM-009 Telegram channel source v1
### RM-010 Telegram group source v1
### RM-011 Telegram comments source v1
### RM-012 Career site source v1
### RM-013 Real sample fixtures

## Phase 2. Input hygiene and protection — DONE

Purpose: defend the pipeline from noisy or malformed raw input before expensive work.

### RM-014 SanitizeNode v1
### RM-015 Raw input validation
### RM-016 URL and origin policy
### RM-017 Quarantine flow

## Phase 3. Early signal shaping — DONE

Purpose: cheaply separate likely-job signal from obvious noise.

### RM-018 Heuristic triage node
### RM-019 Telegram-specific heuristics
### RM-020 Career-site heuristics
### RM-021 Stage conversion reporting

## Phase 4. Dedup and identity — DONE

Purpose: stabilize reruns and avoid inflated job counts.

### RM-022 Raw item identity
### RM-023 Job dedup v1
### RM-024 Near-duplicate detection
### RM-025 Cross-source dedup
### RM-026 Dedup explainability

## Phase 4.5. Architecture hardening — DONE

Purpose: remove extension bottlenecks before extraction, multi-source, and downstream outputs lock the core in place.

### RM-026a Extension registry and plugin discovery
### RM-026b Typed pipeline stages
### RM-026c Declarative CareerSiteConfig source
### RM-026d Sink fan-out and routing
### RM-026e Output and fetch efficiency
### RM-026f Config hygiene

## Phase 5. Extraction and schema quality — DONE

Purpose: turn filtered raw signal into structured jobs.

### RM-027 Job schema v1 finalization
### RM-028 Extraction node v1
### RM-029 LLM provider adapter
### RM-030 Extraction validation layer
### RM-031 Partial extraction strategy
### RM-032 Gold sample evaluation set

## Phase 6. Job quality and relevance — DONE

Purpose: make output useful for the target AI-jobs niche.

### RM-033 Job validation node
### RM-034 AI-role relevance filter
### RM-035 Title and company normalization
### RM-036 Location and work-mode normalization
### RM-037 Compensation parsing v1
### RM-038 Quality scoring

## Phase 7. Outputs and feedback loop — DONE

Purpose: make results consumable by operators and downstream users.

### RM-039 JSON sink hardening
### RM-040 Rejected-items sink
### RM-041 Posting sink v1
### RM-042 Human review output
### RM-043 CLI run modes
### RM-044 Run summary report

## Phase 8. Reliability and failure isolation — DONE

Purpose: survive bad items and partial infrastructure failures.

### RM-045 Per-item fault isolation
### RM-046 Source retry and timeout policy
### RM-047 LLM resilience rules
### RM-048 Sink recovery behavior
### RM-049 Resume and rerun semantics
### RM-050 Operational guards

## Phase 9. Test and regression system — DONE

Purpose: keep velocity without breaking pipeline quality.

### RM-051 Domain and node unit test pack
### RM-052 Pipeline slice integration tests
### RM-053 Real-world regression fixtures
### RM-054 Dedup regression pack
### RM-055 Extraction evaluation harness
### RM-056 Port contract tests

## Phase 10. MVP release contour — DONE

Purpose: make the project usable by someone other than the author.

### RM-057 Env examples and config docs
### RM-058 Runnable README flow
### RM-059 Sample outputs and examples
### RM-060 Source setup guides
### RM-061 Troubleshooting guide
### RM-062 Release checklist

## Phase 11. Multi-source orchestration  ‹PARALLEL-OK: sources are independent›

Purpose: run the pipeline over many sources in a single invocation.

### RM-063 SourceSpec — typed, YAML/JSON-loadable source config
- Add `SourceSpec` as a Pydantic discriminated union to `domain/source_spec.py`.
- Each source type (`telegram_channel`, `telegram_group`, `telegram_comments`,
  `official_api`, `declarative_html`, `server_html`, `rss_feed`, `custom`) has its own
  config model as a union branch.
- `SourceSpec` holds **what** to fetch (URL, entity, parser settings, limits).
  Credentials are never inside `SourceSpec` — see RM-092 (`AuthProvider`).
- `Settings` gains `sources: list[SourceSpec]` replacing single-source fields.
  Old single-source flags remain as deprecated shims.
- Write `load_sources(path: Path) -> list[SourceSpec]` in `application/source_loader.py`
  that reads a YAML or JSON file and validates against the union schema.
- Export a `sources.schema.json` for editor autocompletion.

### RM-063a Declarative CareerSiteConfig
- Promote `CareerSiteConfig` with CSS-selector field mapping to the default path for
  common boards (Greenhouse, Lever board HTML, generic job listing pages).
- Python parser classes remain as fallback for sites needing custom control flow.

### RM-064 CompositeSource adapter
- Implement `CompositeSource(sources: Sequence[Source[RawItem]])` in
  `infrastructure/sources/composite.py`.
- Sequential fan-in: `fetch()` yields items from child sources one by one.
- One child failing does not abort others — failure is quarantined per child.
- Per-source `source_name` / `source_kind` metadata is preserved on each item.
- Add unit tests: ordering, quarantine isolation, empty child handling.

### RM-065 Parallel source fetching
- Extend `CompositeSource` with `concurrency: int = 1` parameter.
- When `concurrency > 1`, fan out with `asyncio.TaskGroup`; merge yielded items
  via an `asyncio.Queue` with bounded capacity.
- One failing source records a `failed` counter increment but does not cancel others.
- Add tests: concurrent fan-out order-independence, error isolation.

### RM-066 Per-source run state namespacing
- Namespace all `Store` keys by source identity: `{source_kind}:{source_name}:{key}`.
- Prevents key collisions when 60+ sources share one store.
- Update `InMemoryStore` and `SQLStore` (Phase 16) to use namespaced keys.

### RM-067 Sources-file CLI integration
- `app.py` reads `Settings.sources` when populated and builds `CompositeSource`.
- Add `--sources-file path.yaml` CLI flag as the primary multi-source entry point.
- `--source-backend` / `--telegram-entity` flags remain for single-source quick runs.

### RM-067a Entry-point discovery
- Load third-party source/parser/sink/store plugins via Python packaging entry points
  (`job_ftch.sources`, `.parsers`, `.sinks`, `.stores`).

### RM-067b Registry v2 — factory signature migration
- Change `create_source(settings: Settings) -> Source` to
  `create_source(spec: SourceSpec, auth: AuthProvider) -> Source`.
- Same migration for `create_sink`, `create_store` — accept spec objects, not Settings.
- `create_source_from_settings(settings: Settings) -> Source` remains as a CLI shim:
  wraps the legacy single-source Settings fields into a `SourceSpec` and calls the new API.
- Add `@register_bypass`, `@register_job_backend`, `@register_search_backend`,
  `@register_embedding_provider`, `@register_vector_backend` to `application/registry.py`.
  Entry point groups: `job_ftch.bypass`, `.job_backends`, `.search_backends`,
  `.embedding_providers`, `.vector_backends`.
- `load_extensions()` extended to load all new groups.
- `create_source(SourceSpec, AuthProvider)` resolves auth inline: calls
  `auth.resolve(spec.auth_source_id)` and passes credentials to the source factory.
- Tests: two `telegram_channel` SourceSpecs → two independent source instances with
  isolated credentials. Old CLI `create_source_from_settings` still passes all existing tests.

## Phase 12. Configurable filter profiles

Purpose: let operators tune signal filtering without touching code.
Replaces all hardcoded keyword lists in `nodes/triage.py` and `nodes/relevance.py`
with config-driven profiles. No keyword changes require code edits.

### RM-074 FilterProfile domain model
- `FilterProfile` in `domain/filter_profile.py`:
  `required_keywords: list[str]`, `exclude_keywords: list[str]`,
  `allowed_source_kinds: list[SourceKind] | None`, `min_text_tokens: int`,
  `min_text_chars: int`, `positive_relevance_keywords: list[str]`,
  `negative_relevance_keywords: list[str]`, `relevance_threshold: float`.
- `positive_relevance_keywords` replaces `_POSITIVE_KEYWORDS` in `nodes/relevance.py`.
- `negative_relevance_keywords` replaces `_NEGATIVE_KEYWORDS` in `nodes/relevance.py`.
- `relevance_threshold` replaces the hardcoded scoring threshold.
- Loaded from YAML/JSON; default values match current hardcoded lists so behaviour
  is identical with no config (backward-compatible).

### RM-075 HeuristicTriageNode and RelevanceNode accept FilterProfile
- `HeuristicTriageNode` and `RelevanceNode` both accept `profile: FilterProfile | None`.
- When `None`, each node falls back to an internal `FilterProfile.default()` that
  mirrors the current hardcoded values.
- `FilterProfile.default()` is the only place hardcoded values live going forward.

### RM-076 Profile loading from config
- `filter_profile_path: Path | None` in `Settings`; loaded from YAML/JSON.

### RM-077 Profile-aware stage conversion reporting
- `RunSummary.applied_profile: str | None`.

### RM-078 Profile regression tests

## Phase 13. Domain model hardening

Purpose: make `JobRecord` the durable, evolvable public contract — not an internal struct that
silently breaks consumers when fields change. Add job lifecycle tracking so dead listings
are automatically detected and marked. Add company entity canonicalization so the same
employer is never split across thousands of name variants.

### RM-134 Job schema versioning and evolution policy
- Add `schema_version` to the public normalized job contract (`JobRecord`) with a stable default.
- Per-field evolution annotation in docstring/ADR: each field tagged as one of:
  `evolve` (additive changes safe), `freeze` (never change name/type), `discard` (marked
  deprecated, removed after two schema_version bumps).
- `JobSchemaPolicy` dataclass in `domain/schema_policy.py`: maps `schema_version` to
  allowed field set. `migrate(raw_dict, from_version, to_version) -> dict` for forward
  migration.
- `PostgreSQLJobBackend.save()` writes `schema_version` to the `jf_jobs` table.
  `PostgreSQLJobBackend.get()` calls `migrate()` if stored version < current.
- `JsonFileSink` writes `schema_version` to each JSON record.
- `search_jobs` and MCP resources return `schema_version` in responses.
- ADR-012: schema versioning and evolution policy.

### RM-135 Job lifecycle — status field and open/filled/expired tracking
- Add `status: JobStatus = JobStatus.OPEN` to the normalized job contract.
  `JobStatus` enum: `open`, `filled`, `expired`, `delisted`, `unknown`.
- Current rollout state: landed partially. `JobStatus` is now part of the public
  canonical contract and `JobLifecycleNode` handles explicit `filled/closed`
  source signals. Cross-run open-job tracking and automatic `delisted` marking
  remain follow-up work.
- `JobLifecycleNode` in `job_ftch/nodes/lifecycle.py`:
  - On each pipeline run per source: compares newly fetched job IDs against
    the set of previously-known-open job IDs stored in `Store` under
    `{tenant_id}:{source_id}:open_job_ids`.
  - Jobs present in store but absent from current fetch → mark `delisted` in
    `PostgreSQLJobBackend`; emit a `JobStatusChangedEvent` to the sink.
  - Jobs with explicit "filled" or "closed" signal in text/metadata → mark `filled`.
  - Configurable grace period: `lifecycle_delist_after_n_missed_runs: int = 2`
    before marking delisted (handles transient scraper misses).
- `RunSummary` gains `delisted: int`, `status_changed: int` counters.
- Integration test: mock source returns 3 jobs run 1, 2 jobs run 2 → 1 marked delisted.

### RM-136 Company entity canonicalization
- `CompanyEntity` in `domain/company.py`:
  `canonical_name: str`, `aliases: list[str]`, `domains: list[str]`,
  `linkedin_url: str | None`, `hh_employer_id: int | None`.
- `CompanyCanonicalizer` in `job_ftch/nodes/company.py`:
  - Normalises company name: strip legal suffixes (ООО, ПАО, Ltd, Inc, LLC, GmbH),
    lowercase, strip punctuation, match against alias table.
  - Alias table loaded from `company_aliases.yaml` (operator-maintained, YAML/JSON config).
  - Fuzzy match (Levenshtein distance ≤ 2) for near-duplicate names; configurable via
    `company_fuzzy_match: bool` in `FilterProfile`.
  - "Сбер" / "Sberbank" / "ПАО Сбербанк" → `canonical_name: "Sberbank"`.
- `JobRecord.company_canonical: str | None` — set by `CompanyCanonicalizer`; `JobRecord.company`
  retains the original extracted name.
- `PostgreSQLJobBackend` indexes `company_canonical` for GROUP BY aggregation queries.
- `search_jobs` (Phase 16) supports `company_canonical` filter parameter.

## Phase 14. Cross-source job aggregation

Purpose: the domain differentiator of a job aggregator is NOT "collect from many sources"
but "one canonical job record enriched from all sources that posted it." Currently dedup
drops duplicates. Phase 14 changes the semantic: same job posted on 5 sources becomes
one aggregate `JobGroup` with preserved source-level `JobRecord` members — not 1 surviving and 4 discarded.

### RM-137 JobGroup domain model
- `JobGroup` in `domain/job_group.py`:
  `canonical_job_id: str`, `jobs: list[JobRecord]` (one per source),
  `canonical_job: JobRecord` (merged best-field view), `source_count: int`,
  `first_seen_at: datetime`, `last_seen_at: datetime`.
- `JobGroup` is NOT a pipeline output type — it lives in the store and is built
  incrementally. The pipeline still emits source-level `JobRecord`; aggregation happens asynchronously.
- `CanonicalJob` = the `JobRecord` with fields merged from all `jobs` in the group:
  longest `description` wins, most fields filled wins, canonical URL = first official API
  URL (if available), `sources: list[SourceAttribution]` preserves all origins.

### RM-138 Cross-source identity matching — post-extraction stage
- `JobIdentityMatcher` in `job_ftch/application/identity.py` operates on `JobRecord` objects
  AFTER extraction and company canonicalization, never on pre-extraction `RawItem`.
- New pipeline stage `JobAggregationNode` in `job_ftch/nodes/aggregation.py`, placed
  AFTER `CompanyCanonicalizer` (Phase 13) in the chain. This is the only place merge happens.
- `DedupNode` (Phase 4) is unchanged: it stays RawItem-level (URL + content + near-dup
  by raw text). Its job is "have I seen this raw item before" — drop exact reruns.
- `JobAggregationNode` matching ladder (on `Job`):
  1. Exact canonical_url match.
  2. company_canonical + normalized title + location fingerprint hash.
  3. Optional fuzzy title match (Levenshtein) for slight title variations.
- On match: `JobGroupStore.merge(existing_group, new_job)` instead of dropping.
  On no match: create a new `JobGroup` with this job as the first member.
- Clear separation documented in ADR: DedupNode = RawItem identity (rerun safety);
  JobAggregationNode = cross-source Job identity (one vacancy from many sources).
- Tests: a job that passes DedupNode (new raw item) but matches an existing JobGroup
  is merged, not dropped.

### RM-139 JobGroupStore protocol and PostgreSQL implementation
- `JobGroupStore` protocol in `job_ftch/application/contracts.py`:
  `get_group(canonical_job_id: str) -> JobGroup | None`,
  `merge(group_id: str, new_job: Job) -> JobGroup`,
  `list_groups(tenant_id: str, limit: int) -> list[JobGroup]`.
- `PostgreSQLJobGroupBackend` in `infrastructure/backends/jobs/postgres_groups.py`.
  Schema: `jf_job_groups(group_id PK, canonical_job JSONB, source_count, ...)`,
  `jf_job_group_members(group_id FK, job_id FK, source_kind, source_name, url)`.
- `JobAggregationNode` (RM-138) calls this backend to perform the merge.
- `search_jobs` (Phase 16) returns `JobGroup` when `group_by=true` query param.
- MCP tool `search_jobs` (Phase 24) default returns groups (canonical job + source count).

### RM-140 Aggregation regression tests
- Fixture: same job text from `telegram_channel`, `greenhouse_api`, `career_site_html`.
- Run pipeline 3 times (once per source).
- Assert: 1 `JobGroup` with `source_count=3`, `canonical_job` has all 3 sources.
- Assert: `JobAggregationNode` correctly identifies matches across sources.
- Assert: `search_jobs` returns 1 result, not 3.
- Assert: `RunSummary.duplicate_merged: int = 2` (not `duplicates: int = 2`).

## Phase 15. Persistent store

Purpose: survive restarts, accumulate dedup history, and support reruns across sessions.
Architecture: universal `StoreConnector` protocol → DBMS-agnostic SQL adapter layer →
SQLite (single file, default, lightweight) and PostgreSQL (server, scale) are both first-class.
PostgreSQLJobGroupBackend and the JobGroup schema are designed here together with PostgreSQLJobBackend.

### RM-068 StoreConnector protocol — universal interface
- Define `StoreConnector` protocol in `application/contracts.py` with a minimal
  key-value + set surface:
  `get(key) -> str | None`, `set(key, value)`, `delete(key)`,
  `set_add(key, member)`, `set_contains(key, member) -> bool`,
  `set_members(key) -> frozenset[str]`, `ping() -> bool`.
- This is the universal connector — any backend (SQL, Redis, filesystem) implements it.
- `Store` protocol (domain port) stays unchanged; `StoreConnector` lives in infra.
- `InMemoryStore` is refactored to implement `StoreConnector` directly; remains the
  default for tests and single-run CLI mode.

### RM-068a SQLiteStore — lightweight default (single file, zero infra)
- `SQLiteStore` in `infrastructure/stores/sqlite.py` implementing `StoreConnector`
  via the `SQLStoreAdapter` (RM-069) with an `aiosqlite` connection.
- Single-file database: `store_backend=sqlite`, `store_path=.runtime/job_ftch.db`.
- Default persistent backend for self-hosted single-node use — survives restarts,
  zero external service. `aiosqlite` is a tiny dependency (no server).
- Reuses the DBMS-agnostic `SQLStoreAdapter`; proves the adapter is truly driver-agnostic.
- `CREATE TABLE IF NOT EXISTS` on startup; same `jf_kv` / `jf_set` schema.

### RM-069 SQLStoreAdapter — DBMS-agnostic SQL layer
- `SQLStoreAdapter` in `infrastructure/stores/sql_adapter.py`.
- Sits between `StoreConnector` interface and any SQL backend.
- Schema (DBMS-agnostic DDL):
  - `jf_kv(key TEXT PK, value TEXT, updated_at TIMESTAMPTZ)`
  - `jf_set(key TEXT, member TEXT, PRIMARY KEY (key, member))`
- All queries use parameterised placeholders (`$1` / `%s` depending on driver).
- `SQLStoreAdapter` receives an injected `AsyncConnection` — it is driver-agnostic;
  the connection factory is provided by the concrete backend.
- `CREATE TABLE IF NOT EXISTS` + index on `key` on startup.
- Implements namespaced keys: `{tenant_id}:{source_kind}:{source_name}:{key}`.

### RM-070 PostgreSQLStore — scale implementation
- `PostgreSQLStore` in `infrastructure/stores/postgres.py`.
- Uses `asyncpg` (async, no ORM, fastest Postgres driver for Python).
- Connection pool: `asyncpg.create_pool(dsn, min_size=2, max_size=10)`.
- Injects `asyncpg.Connection` into `SQLStoreAdapter` per operation.
- Config: `store_backend=postgres`, `store_dsn` (full DSN string resolved via
  `AuthProvider` — never hardcoded); `store_pool_min`, `store_pool_max`.
- `ping()` executes `SELECT 1`; returns `False` on connection error.
- On `ping()` failure at pipeline startup: warn + fall back to `InMemoryStore`
  (configurable; `store_fallback_on_error: bool = True`).
- Add `store_backend=sqlite` as the documented default for self-hosted in `.env.example`.

### RM-071 Store migration path
- Alembic or plain SQL migration files in `infrastructure/stores/migrations/`.
- `job_ftch store migrate` CLI command applies pending migrations.
- `job_ftch store reset --tenant <id>` clears all keys for a tenant namespace.
- Migration guard: pipeline startup checks schema version; refuses to run if behind.

### RM-072 Store health check and fallback
- `ping() -> bool` already on `StoreConnector` (RM-068).
- Pipeline startup: `ping()` → on failure log `warn` and fall back to `InMemoryStore`
  with a `store_degraded=true` flag in `RunSummary`.
- Health endpoint (Phase 25 Telegram bot/FastAPI bridge, RM-128): `GET /health` returns store status.

### RM-073 Store-backed idempotency regression tests
- Run same fixture twice with `PostgreSQLStore` (testcontainers-python Postgres instance).
- Second run emits 0 items; dedup keys persist across `PostgreSQLStore` instantiations.
- Verify tenant namespace isolation: two tenants with identical item IDs do not collide.
- Verify `SQLStoreAdapter` is independently testable with a SQLite in-memory connection
  (substitute driver) — confirms DBMS-agnostic design.

## Phase 16. Fulltext and semantic search layer  ‹fulltext = core (Postgres FTS); semantic/vector = HEAVY, optional extras›

Purpose: make collected jobs queryable without exporting raw JSON.
Search is built group-aware from day one (search_jobs returns JobGroup by default).
Architecture mirrors Phase 15: universal protocols → DBMS-agnostic adapters → concrete implementations.
SQLite (single file, default, lightweight) and PostgreSQL (server, scale) are both first-class; vector backends are optional plugins.

### RM-079 JobPersistenceBackend protocol
- Define `JobPersistenceBackend` protocol in `application/contracts.py`:
  `save(job: Job) -> None`, `get(job_id: str) -> Job | None`,
  `list(tenant_id: str, limit: int, offset: int) -> list[Job]`,
  `delete(job_id: str) -> None`, `ping() -> bool`.
- Lives alongside `StoreConnector` — separate protocol because jobs are domain
  objects (typed), while `StoreConnector` is a raw key-value/set store.
- `JobPersistenceBackend` is registered via `@register_job_backend(name)` decorator.

### RM-079a SQLiteJobBackend + FTS5 — lightweight search tier
- `SQLiteJobBackend` in `infrastructure/backends/jobs/sqlite.py` implementing
  `JobPersistenceBackend` and `SearchBackend`.
- Uses SQLite FTS5 virtual table for fulltext: `jf_jobs_fts(title, company, description)`.
- Zero extra infrastructure — FTS5 ships with stdlib sqlite3 / aiosqlite.
- Default search backend for single-node deployments; PostgreSQL FTS / pgvector for scale.
- `JobGroup` persisted as JSON in a `jf_job_groups` table (group-aware, matching the
  Postgres backend's surface).

### RM-080 PostgreSQLJobBackend — scale implementation
- `PostgreSQLJobBackend` in `infrastructure/backends/jobs/postgres.py`.
- Uses `asyncpg` connection pool (shared with `PostgreSQLStore` from Phase 15).
- Schema: `jf_jobs(job_id PK, tenant_id, source_kind, source_name, title, company,
  raw_json JSONB, fts_vector TSVECTOR, created_at, updated_at)`.
- `fts_vector` column populated via `to_tsvector('english', title || ' ' || company
  || ' ' || raw_json->>'description')` on INSERT/UPDATE trigger.
- GIN index on `fts_vector` for fast fulltext lookups.
- Config: `job_backend=postgres`; reuses `store_dsn` from Phase 15 — no second DSN.

### RM-081 SearchBackend protocol
- Define `SearchBackend` protocol in `application/contracts.py`:
  `search(query: str, tenant_id: str | None, limit: int) -> list[JobGroup]`.
- Implementations registered via `@register_search_backend(name)`.
- Two initial implementations:
  - `PostgreSQLFTSBackend` — uses `tsvector` + `plainto_tsquery`; zero extra infra;
    built on top of `PostgreSQLJobBackend`'s connection pool.
  - `PgVectorBackend` — uses `pgvector` extension; `[pgvector]` extras group;
    requires `CREATE EXTENSION vector` on the Postgres instance; hybrid search
    (FTS + cosine similarity) with `RRF` (Reciprocal Rank Fusion) merging.
- `SearchBackend` is injected into MCP tools (`search_jobs`), CLI, and bot handler.
  None of those callers know which implementation is active.

### RM-082 EmbeddingProvider protocol
- Define `EmbeddingProvider` protocol in `application/contracts.py`:
  `embed(texts: list[str]) -> list[list[float]]`, `dimensions: int`.
- Implementations in `infrastructure/embeddings/`:
  - `OpenAIEmbeddingProvider(model="text-embedding-3-small")` — uses existing
    `openai` dep; `[openai]` extras already in core.
  - `SentenceTransformersProvider(model_name)` — local inference, no API key;
    `[embeddings]` extras group (`sentence-transformers`, `torch`); heavy optional dep.
  - `OllamaEmbeddingProvider(base_url)` — local Ollama server; `[ollama]` extras.
- Auth (API keys) always resolved via `AuthProvider`, never hardcoded.
- `EmbeddingProvider` registered via `@register_embedding_provider(name)`.

### RM-083 VectorBackend protocol and QdrantBackend
- Define `VectorBackend` protocol in `application/contracts.py`:
  `upsert(job_id: str, vector: list[float], payload: dict) -> None`,
  `search(vector: list[float], limit: int, filter: dict | None) -> list[str]`
  (returns job_ids; caller fetches `Job` objects from `JobPersistenceBackend`).
- Implementations:
  - `QdrantBackend` in `infrastructure/backends/vector/qdrant.py` — first concrete
    implementation; uses `qdrant-client` (async); `[qdrant]` extras group.
    Config: `vector_backend=qdrant`, `qdrant_url`, `qdrant_api_key` via `AuthProvider`.
    Supports both local Qdrant (in-process or Docker) and Qdrant Cloud.
  - `PgVectorBackend` — second implementation; shares Postgres instance from Phase 15;
    zero extra infrastructure; `[pgvector]` extras. Recommended default for single-node
    deployments; Qdrant recommended for high-volume or dedicated vector workloads.
- `VectorBackend` is optional: if not configured, `SearchBackend` falls back to FTS only.

### RM-084 Embedding pipeline integration
- `EmbeddingNode` in `nodes/embedding.py` — optional pipeline stage after `ExtractionNode`.
- Calls `EmbeddingProvider.embed([job.title + ' ' + job.description])`.
- The embedding is written only to `VectorBackend.upsert(job_id, vector)` keyed by `job_id`. The domain `Job` model never carries the raw vector — embeddings are an infrastructure concern, retrieved via `VectorBackend`, not via the domain object.
- Gated by `embedding_enabled: bool = False` in `Settings`; off by default.

### RM-085 CLI and MCP search commands
- `job_ftch search "<query>" [--tenant <id>] [--backend fts|vector|hybrid] [--limit N]`
  — delegates to registered `SearchBackend`; outputs formatted table or JSON (returning JobGroups by default).
- `--backend hybrid` runs both `PostgreSQLFTSBackend` and `VectorBackend` in parallel,
  merges with RRF scoring.
- MCP tool `search_jobs` (Phase 24 RM-122) uses same `SearchBackend` injection.
- Telegram bot `/search` command (Phase 25 RM-129) uses same injection.
- Search result export: `--output jobs.jsonl` writes `list[Job]` as JSONL.

## Phase 17. Scheduler and daemon mode  ‹PARALLEL-OK›

Purpose: run the pipeline continuously over many sources without manual CLI invocations.

### RM-086 Run interval config
- `schedule_interval_seconds: int | None` in `Settings`.
- Per-source override: `interval_seconds: int | None` in `SourceSpec`.

### RM-087 Asyncio scheduler
- `Scheduler` in `application/scheduler.py`; pure asyncio, no Celery/APScheduler.
- Per-source-group intervals via `asyncio.sleep`.
- Respects `SIGINT` / `SIGTERM` → drain in-flight → exit.

### RM-088 Daemon CLI mode
- `--daemon` flag starts `Scheduler` instead of a single `Pipeline.run`.
- PID file in `JOB_FTCH_RUN_DIR`.

### RM-089 Rate-limit and backoff policy
- `rate_limit: RateLimitConfig` in `SourceSpec`: `min_interval_seconds`,
  `backoff_multiplier`.
- `Scheduler` enforces before dispatching; prevents TG flood-wait and HTTP 429.

### RM-090 Scheduler observability
- `RunSummary.scheduled_run_index`, `source_id`.
- `--status` CLI flag reads last run from store.

### RM-091 Scheduler regression and load tests

## Phase 18. Source configuration system v2 — credentials, ingestion modes, bypass  ‹auth/ingest = core; bypass = HEAVY/RISKY, COMMUNITY-MAINTAINED›

Purpose: clean separation of source config, credentials, ingestion strategy, and protection
bypass. Modelled after the dlt secrets / source / destination separation pattern.

### RM-092 AuthProvider protocol
- Define `AuthProvider` protocol in `application/contracts.py`:
  `resolve(source_id: str) -> dict[str, str]`.
- Implementations in `infrastructure/auth/`:
  - `EnvAuthProvider` — reads `JOB_FTCH_AUTH_{SOURCE_ID}_{KEY}` env vars.
  - `FileAuthProvider` — reads a secrets YAML/JSON file (gitignored).
  - `VaultAuthProvider` — stub for HashiCorp Vault / AWS Secrets Manager integration.
- `SourceSpec` carries `auth_provider: str | None` and `auth_source_id: str | None`
  as lookup keys only. No raw credentials in `SourceSpec` or YAML config files.
- Rule: credentials are NEVER serialised into `SourceSpec`. AuthProvider resolves at
  runtime, like `dlt.secrets.value`.

### RM-093 IngestMode protocol
- Define `IngestMode` protocol in `application/contracts.py`.
- Implementations in `infrastructure/ingest/`:
  - `PollingMode(interval_seconds)` — default; wraps `Source.fetch()` on a timer.
  - `EventListenerMode` — infinite async generator; source emits items as events arrive
    (e.g. Telethon `@client.on(events.NewMessage)`).
  - `RSSMode(feed_url, etag_store)` — polls feed URL; uses etag/last-modified for
    incremental dedup without re-processing seen entries.
  - `WebhookMode(host, port, path)` — embedded HTTP listener (aiohttp); receives push
    and yields items.
  - `WebSocketMode(ws_url)` — persistent WS connection as async generator.
- `IngestMode` wraps a `Source` and controls HOW `fetch()` is called; it does not
  change the `RawItem` contract.
- `Scheduler` delegates interval logic to `IngestMode` when `EventListenerMode` or
  `WebSocketMode` is active (no polling tick needed).

### RM-094 BypassStrategy protocol
- Define `BypassStrategy` protocol in `application/contracts.py`:
  `configure(client: Any) -> Any`.
- Implementations in `infrastructure/bypass/`:
  - `NoopBypass` — default; passes client through unchanged.
  - `ProxyRotatorBypass(proxy_list)` — injects rotating HTTP proxies into httpx client.
  - `StealthBrowserBypass` — applies `playwright-stealth` patches to a Playwright page.
  - `CaptchaSolverBypass(provider, api_key)` — integrates Capsolver or 2captcha for
    CAPTCHA challenges; `provider` is pluggable.
  - `BehaviorSimBypass(delays, scroll)` — adds random delays and scroll events to
    simulate human interaction; used together with `StealthBrowserBypass`.
  - `ManagedScraperBypass(api_url, api_key)` — delegates HTTP fetch to Scrapfly, ZenRows, or Browserless via their HTTP proxy API.
- `BypassStrategy` is referenced in `SourceSpec` as `bypass: str | None`; the registry
  resolves the implementation by name.
- Rule: `BypassStrategy` is NEVER imported in `domain/` or `application/pipeline.py`.
  It is infrastructure-level only, configured per-source.

### RM-095 BypassStrategy registry and per-source config
- Extend `application/registry.py` with `@register_bypass(name)` decorator.
- `SourceSpec` gains `bypass: str | None` and `bypass_config: dict | None`.
- `CareerSiteSource`, `BrowserSource`, and `HardScraperSource` accept an optional
  `BypassStrategy` injected at build time.

### RM-096 Source config schema and documentation
- Export `config/sources.schema.json` (JSON Schema) generated from `SourceSpec` union.
- Add `config/sources.example.yaml` with one example per source type.
- Document the full config reference in `docs/source_config.md`.

## Phase 19. Official API sources  ‹PARALLEL-OK: each adapter independent›

Purpose: right-path adapters for job boards that offer structured APIs. Preferred over
HTML scraping wherever available: stable, no bypass needed, structured output.

### RM-097 OfficialAPISource base class
- `OfficialAPISource` in `infrastructure/sources/api/base.py`.
- Handles pagination (cursor, offset, link-header), rate limiting (`asyncio.sleep`),
  field mapping via a configurable `field_map: dict[str, str]`, and incremental
  cursor (`last_cursor` persisted in `Store` under namespaced key).
- Auth injected via `AuthProvider`; no credentials hardcoded.
- Pattern mirrors dlt `rest_api_source` config: dict-driven endpoint + pagination config.

### RM-098 Declarative REST API source
- `RestAPISourceSpec` branch in `SourceSpec` discriminated union.
- Config: `base_url`, `jobs_endpoint`, `pagination` (cursor/offset/link-header),
  `field_map`, `headers`, `params`, `incremental_cursor_field`.
- Covers any JSON-returning jobs API without writing Python: Greenhouse API, Lever API,
  Remotive, Adzuna, custom ATS endpoints.
- Add `@register_source("rest_api")` factory that builds from spec dict.

### RM-099 Greenhouse API adapter
- `GreenhouseAPISource` in `infrastructure/sources/api/greenhouse.py`.
- Endpoint: `https://api.greenhouse.io/v1/boards/{slug}/jobs?content=true`.
- No auth required for public boards; optional `api_key` via `AuthProvider` for private.
- Maps JSON response fields to `RawItem` metadata via `OfficialAPISource.field_map`.
- Add contract test against recorded fixture.

### RM-100 HH.ru / HeadHunter API adapter
- `HHAPISource` in `infrastructure/sources/api/hh.py`.
- Public API: `https://api.hh.ru/vacancies` with query params and cursor pagination.
- Rich metadata: salary, location, employment type — map directly to `Job` fields.
- Add contract test against recorded fixture.

### RM-101 Lever API adapter
- `LeverAPISource` in `infrastructure/sources/api/lever.py`.
- Endpoint: `https://api.lever.co/v0/postings/{company}?mode=json`.
- No auth for public postings.
- Add contract test against recorded fixture.

## Phase 20. Browser and hard scraper sources  ‹HEAVY/RISKY · COMMUNITY-MAINTAINED (best-effort, does not gate core)›

Purpose: reach JS-rendered, CloudFlare-protected, and behavior-gated job pages.
Browser automation is an optional, heavyweight dependency — never imported in core.

### RM-102 BrowserSource base class
- `BrowserSource` in `infrastructure/sources/browser/base.py`.
- Wraps `playwright` (optional import, guarded by `TYPE_CHECKING` and try/except).
- Accepts a `BypassStrategy` (e.g. `StealthBrowserBypass`, `ManagedScraperBypass`).
- `fetch()` launches browser context, navigates, extracts, yields `RawItem` records,
  closes context.
- `playwright` is NOT added to core `pyproject.toml` dependencies; installed on demand
  via `uv add playwright` or the `browser` extras group.
- Add `BrowserSourceSpec` branch to `SourceSpec` union.

### RM-103 Stealth + proxy bypass integration
- `StealthBrowserBypass`: applies `playwright-stealth` (v2+) patches on page context.
- `ProxyRotatorBypass`: injects proxy from a rotating list into Playwright launch args.
- Combine for sites with JS challenges and IP reputation filters.
- Document clearly: stealth does NOT bypass TLS fingerprinting or behavioural server-side
  analysis. For production CloudFlare sites, `ManagedScraperBypass` is recommended.

### RM-104 HardScraperSource
- `HardScraperSource` in `infrastructure/sources/browser/hard_scraper.py`.
- Full pipeline within source: sniffer (detect ATS/board type) → scraper (Playwright
  fetch + optional sniffer headers) → parser (selectolax or custom) → behavior sim.
- Accepts `BypassStrategy`; `BehaviorSimBypass` adds delays, scroll, mouse jitter.
- Used for sites where declarative HTML parsing fails and official API does not exist.

### RM-105 Managed scraper API bypass
- `ManagedScraperBypass(api_url, api_key)` in `infrastructure/bypass/managed.py`.
- Delegates HTTP fetch to Scrapfly, ZenRows, or Browserless via their HTTP proxy API.
- Recommended production path for CloudFlare-protected sites — avoids maintaining own
  browser farm and proxy rotation.
- `api_key` always resolved via `AuthProvider`, never in config files.

## Phase 21. Realtime and push ingestion  ‹HEAVY: long-running connections›

Purpose: replace polling with push or event-listener where the source supports it.
Reduces latency from 3 hours to seconds for Telegram; enables webhook-driven career sites.

### RM-106 TelegramRealtimeSource
- `TelegramRealtimeSource` in `infrastructure/sources/telegram.py`.
- Uses Telethon `@client.on(events.NewMessage(chats=[...]))` event handler.
- `fetch()` is an infinite async generator: yields `RawItem` as messages arrive.
- Accepts a list of channel/group entities — one client handles all.
- `IngestMode` for this source is `EventListenerMode`; `Scheduler` does not poll it.
- Graceful shutdown: unregisters handlers on `SIGINT`/`SIGTERM`.
- Add integration test with fake Telethon event bus.

### RM-107 RSSFeedSource
- `RSSFeedSource` in `infrastructure/sources/feeds/rss.py`.
- Uses `httpx` + `feedparser`; sends `If-None-Match` / `If-Modified-Since` headers.
- Incremental: stores last seen `entry.id` set in `Store`; yields only new entries.
- Covers job boards that publish RSS (RemoteOK, WeWorkRemotely, Stack Overflow Jobs).
- `feedparser` added as optional dep in `feeds` extras group.

### RM-108 WebhookSource
- `WebhookSource` in `infrastructure/sources/realtime/webhook.py`.
- Embedded `aiohttp` HTTP server (optional dep, `realtime` extras group).
- `IngestMode` = `WebhookMode`; `fetch()` is an infinite generator listening on port.
- Validates incoming payloads against a configurable JSON Schema.
- Useful for ATS platforms that support webhook-on-new-job-posting (Lever, Workable).

### RM-109 WebSocketSource
- `WebSocketSource` in `infrastructure/sources/realtime/websocket.py`.
- Persistent WebSocket connection using `websockets` library (optional, `realtime` group).
- `fetch()` is an infinite async generator; reconnects on disconnect with exponential
  backoff.
- Maps incoming frames to `RawItem` via a configurable field mapping.

## Phase 22. Library packaging and runtime adapters  ‹LIBRARY-FIRST: start RM-110 namespace EARLY (can begin right after Phase 11)›

Purpose: make `job_ftch` a proper installable library — clean namespace, programmatic API,
zero namespace pollution, and enforceable module boundaries. BLOCKER for any public release.
Core never imports CLI, FastStream, Dagster, or any orchestrator.
Adapters live in `adapters/` or a separate repository.

### RM-110 Package namespace restructuring — BLOCKER
- Move ALL source files under a `job_ftch/` top-level package:
  `job_ftch/domain/`, `job_ftch/application/`, `job_ftch/nodes/`,
  `job_ftch/sinks/`, `job_ftch/infrastructure/`.
- After this, `import domain` / `import nodes` / `import sinks` no longer work —
  only `from job_ftch.domain import Job`, `from job_ftch.nodes import ExtractionNode`.
  This prevents namespace collision with any consumer's own `domain/`, `nodes/`, `sinks/`.
- `pyproject.toml`: `packages = ["job_ftch"]` (single entry). Remove the current
  multi-package list.
- `app.py` → `job_ftch/cli.py`; entry point: `job_ftch = "job_ftch.cli:main"`.
- All internal imports updated (mechanical find-and-replace + CI green).
- CI gate: `grep -r "^from domain\|^import domain\|^from nodes\|^from sinks\|^from application\|^from infrastructure" --include="*.py" .` outside `job_ftch/` must return empty.
- Add `pyproject.toml` extras: `[browser]`, `[feeds]`, `[realtime]`, `[pgvector]`,
  `[qdrant]`, `[embeddings]`, `[mcp]`, `[bot]`, `[all]`.

### RM-110a PipelineBuilder — programmatic API
- `PipelineBuilder` in `job_ftch/application/builder.py`:
  ```
  job_ftch.PipelineBuilder()
      .source(spec: SourceSpec)          # or .sources(list[SourceSpec])
      .auth(provider: AuthProvider)
      .stage(node: ProcessingNode)       # chained, order preserved
      .sink(sink: Sink)
      .store(store: StoreConnector)
      .schedule(interval_seconds: int)
      .build() -> Pipeline
  ```
- Builder validates: sanitize node must be first; at least one source; at least one sink.
- `job_ftch.configure(path: Path) -> PipelineBuilder` — loads `TenantConfig` from YAML
  and returns a pre-configured builder, covering the 80% case with zero boilerplate.
- `job_ftch.run(path: Path) -> RunSummary` — one-liner for scripts:
  `import job_ftch; job_ftch.run("config/ai_jobs.yaml")`.
- CLI (`job_ftch/cli.py`) is a thin wrapper over `PipelineBuilder`; no pipeline
  construction logic lives in `cli.py` itself.

### RM-110b Module boundary CI enforcement
- Add `scripts/check_module_boundaries.py` that parses import graph and asserts:
  - `job_ftch/domain/**` imports nothing outside stdlib/pydantic.
  - `job_ftch/application/**` imports nothing outside domain + stdlib + pydantic.
  - `job_ftch/nodes/**` imports nothing from `infrastructure/`, `adapters/`.
- Run as `pre-commit` hook and in CI (`ruff` import rules + custom script).
- Add `ADR-011: module boundary enforcement` documenting the rules.

### RM-111 FastStream adapter
- `adapters/faststream_adapter.py` (in a separate repo or `adapters/` directory).
- `@broker.subscriber` receives a trigger message; calls `Pipeline.run()` with a
  `SourceSpec` deserialized from the message payload.
- `@broker.publisher` emits extracted `JobRecord` records downstream.
- Supports NATS JetStream, Redis Streams, Kafka, RabbitMQ via FastStream's unified API.
- One `Pipeline` per message; `CompositeSource` handles multi-source triggers.
- Add example in `docs/adapters/faststream.md`.

### RM-112 Dagster adapter
- `adapters/dagster_adapter.py`.
- Each `SourceSpec` becomes a Dagster `asset`; `Pipeline.run()` is the materialisation
  function.
- `RunSummary` maps to Dagster `AssetMaterialization` metadata.
- `PostgreSQLStore` or any `StoreConnector` maps to Dagster `IOManager`.
- Add example in `docs/adapters/dagster.md`.

### RM-113 MCP server adapter (FastMCP)
- `adapters/mcp_adapter.py`.
- Exposes `run_pipeline(source_spec: dict) -> RunSummary` as an MCP tool via FastMCP.
- Exposes `search_jobs(query: str) -> list[JobGroup]` as an MCP resource (Phase 16).
- Enables AI agents to trigger ingestion and query results via MCP protocol.
- Add example in `docs/adapters/mcp.md`.

### RM-114 FastAPI adapter
- `adapters/fastapi_adapter.py`.
- `POST /pipeline/run` — triggers `Pipeline.run()` with body as `SourceSpec` JSON.
- `GET /pipeline/status` — reads last `RunSummary` from `Store`.
- `GET /jobs/search?q=...` — delegates to Phase 16 search.
- Add example in `docs/adapters/fastapi.md`.

## Phase 23. Multi-tenant and multi-instance

Purpose: let one installation serve multiple independent job search profiles ("bots"), each
with isolated sources, store, credentials, and output. One machine, N tenants, zero cross-
contamination.

### RM-115 TenantConfig model
- Add `TenantConfig` in `domain/tenant.py`:
  `tenant_id: str`, `display_name: str`, `sources: list[SourceSpec]`,
  `output: OutputSpec`, `schedule: ScheduleSpec | None`, `auth_provider: str | None`.
- `tenant_id: str` is a slug used as namespace prefix everywhere — store keys, sink paths,
  log fields, MCP resource URIs.
- `OutputSpec` unifies sink backend + path/table + format into one config block.
- `ScheduleSpec` carries per-tenant `interval_seconds` and `ingest_mode` overrides.

### RM-116 Tenant config loader
- `load_tenants(configs_dir: Path) -> list[TenantConfig]` in
  `application/tenant_loader.py`.
- Scans `*.yaml` / `*.json` files in a directory; validates each against `TenantConfig`.
- Supports a single-file multi-tenant format: top-level `tenants:` list key.
- `Settings` gains `configs_dir: Path | None`; CLI gains `--configs-dir` flag.

### RM-117 Tenant-isolated store and sink namespacing
- All `Store` keys prefixed with `tenant_id:` — extends RM-066 source namespacing.
- `JsonFileSink`, `PostgreSQLJobSink`, and `PostgreSQLStore` paths/schemas interpolated
  with `{tenant_id}` from config.
- `RunSummary` gains `tenant_id: str` field; reported in logs and MCP status tools.

### RM-118 Multi-tenant pipeline runner
- `TenantRunner` in `application/tenant_runner.py`.
- Builds one `Pipeline` + `CompositeSource` per tenant from `TenantConfig`.
- `run_all(tenants, concurrency=N)` — asyncio fan-out across tenants; each tenant is
  independent; one tenant failing does not affect others.
- `Scheduler` (Phase 17) extended: per-tenant interval from `TenantConfig.schedule`.

### RM-119 Multi-instance isolation contract
- Two instances with the same `configs_dir` must not corrupt each other's store.
- `PostgreSQLStore` relies on Postgres MVCC for concurrent write safety; connection pool
  serialises writes at transaction level.
- Integration test: two `TenantRunner` instances running same tenant concurrently →
  no duplicate items in output, no store corruption.

### RM-120 Tenant management CLI
- `job_ftch tenants list` — prints all tenant IDs, display names, source counts.
- `job_ftch tenants status <tenant_id>` — last `RunSummary` from store.
- `job_ftch tenants run <tenant_id>` — single-tenant run, bypasses Scheduler.
- `job_ftch tenants reset <tenant_id>` — clears store namespace for that tenant.

## Phase 24. FastMCP server — universal AI client interface

Purpose: expose the full job_ftch surface as an MCP server compatible with every major
AI coding assistant and agentic runtime. Users install once, connect from any tool.

### RM-121 FastMCP server core
- `adapters/mcp/server.py` — FastMCP application with lifecycle hooks.
- Startup: loads all tenants from `configs_dir`; builds `TenantRunner`; starts
  `Scheduler` in background task.
- Shutdown: drains in-flight pipelines; flushes sinks; closes store connections.
- Transport: stdio (default, for Claude Desktop / Cursor / Codex CLI) and
  SSE/HTTP (for remote connections, Claude Code, OpenCode, pi, Antigravity CLI,
  Antigravity IDE, Antigravity App, Codex App).
- Auth: optional Bearer token for SSE transport (env var `JOB_FTCH_MCP_TOKEN`).

### RM-122 MCP tools surface
- `run_pipeline(tenant_id: str) -> RunSummary` — trigger immediate ingest for one tenant.
- `run_all_pipelines() -> list[RunSummary]` — trigger all tenants in parallel.
- `get_status(tenant_id: str) -> RunSummary` — last run result from store.
- `list_tenants() -> list[TenantInfo]` — all loaded tenants with last-run metadata.
- `search_jobs(query: str, tenant_id: str | None, limit: int) -> list[JobGroup]` — fulltext
  search (Phase 16); `tenant_id=None` searches across all tenants.
- `get_job(job_id: str) -> JobRecord` — fetch single job by stable ID.
- `reset_tenant(tenant_id: str) -> None` — clear dedup store for a tenant (admin tool).

### RM-123 MCP resources surface
- `jobs://{tenant_id}/latest` — last N extracted jobs as JSON array.
- `jobs://{tenant_id}/run_summary` — last `RunSummary` as structured resource.
- `config://{tenant_id}` — sanitised `TenantConfig` (credentials stripped).
- Resources are read-only; mutations happen only through tools.

### RM-124 Client compatibility matrix
- **stdio transport**: Claude Desktop, Cursor, Codex CLI, Codex App, Antigravity CLI,
  Antigravity IDE, Antigravity App, pi, OpenCode. No network config needed.
- **SSE/HTTP transport**: Claude Code (`claude mcp add`), remote agents, CI runners.
- Test matrix: integration tests for stdio and SSE transports using FastMCP test client.
- Document `claude_desktop_config.json`, `~/.cursor/mcp.json`, `mcp.json` (Codex)
  snippets for each client in `docs/mcp/client_setup.md`.

### RM-125 MCP skill packaging
- Package `job_ftch` as a Claude Code skill in `skill/`:
  - `skill/skill.md` — skill manifest: name, description, invocation trigger.
  - `skill/handler.py` — routes skill invocations to MCP tool calls.
  - Skill trigger: `/job-search`, `/jobs`, `/run-jobs`.
- Document skill installation in `docs/mcp/skill_install.md`.

### RM-126 MCP plugin packaging
- `pyproject.toml` extras group `[mcp]`: `fastmcp`, `uvicorn`.
- Entry point `job_ftch.mcp_servers`: `default = job_ftch_mcp:create_server`.
- Supports MCP plugin discovery by compatible runtimes (Antigravity App, future
  Claude plugin ecosystem).
- `job_ftch mcp-server` CLI command starts the server with auto-detected transport.

### RM-127 MCP server deployment guide
- `docs/mcp/deploy.md`: local stdio, local SSE, Docker (single-container), systemd unit.
- `Dockerfile.mcp`: minimal image with `job_ftch[mcp,realtime]` + configs volume mount.
- Environment variable reference for all MCP server settings.

## Phase 25. Telegram bot client + FastAPI webhook bridge

Purpose: let users interact with job_ftch entirely through Telegram — trigger ingestion,
receive job digests, search vacancies — via a Telegram bot backed by a FastAPI service
that bridges Telegram ↔ job_ftch core.

### RM-128 FastAPI webhook bridge
- `adapters/telegram_bot/api.py` — FastAPI application.
- `POST /webhook/telegram` — receives Telegram Bot API updates; validates secret token
  in `X-Telegram-Bot-Api-Secret-Token` header.
- `POST /pipeline/run` — HTTP trigger for ingest (internal; called by bot handler).
- `GET /pipeline/status/{tenant_id}` — returns last `RunSummary` as JSON.
- `GET /jobs/search` — fulltext search endpoint delegated to Phase 16 layer.
- FastAPI lifespan: on startup loads `TenantRunner`; on shutdown drains pipelines.
- Auth: Telegram secret token + optional API key for internal endpoints.

### RM-129 Telegram bot handler
- `adapters/telegram_bot/bot.py` — python-telegram-bot v21+ (async) or aiogram v3.
- Commands:
  - `/start` — welcome message; list configured tenants.
  - `/run [tenant_id]` — trigger pipeline for one or all tenants; reply with progress.
  - `/status [tenant_id]` — show last `RunSummary` (items found, quarantined, timing).
  - `/search <query> [tenant_id]` — fulltext job search; paginated inline results.
  - `/digest [tenant_id]` — send latest N jobs as formatted messages.
  - `/tenants` — list all tenants with status badges.
  - `/reset <tenant_id>` — admin command (restricted by `allowed_user_ids`).
- Inline keyboard: job cards with "Details", "Open URL", "Next page" buttons.
- Rate limiting: per-user command throttle to prevent accidental flood.

### RM-130 Job digest formatter
- `adapters/telegram_bot/formatter.py`.
- Formats `JobRecord` records as Telegram HTML messages: title, company, location, salary
  range, work mode, source, URL button.
- Truncates long descriptions; collapses multiple locations.
- Digest mode: N jobs per message, pagination via inline keyboard callback queries.

### RM-131 Bot user access control
- `allowed_user_ids: list[int]` and `allowed_chat_ids: list[int]` in bot config
  (resolved via `AuthProvider`; never hardcoded).
- Admin-only commands: `/run`, `/reset` — checked against `admin_user_ids`.
- Unknown users receive a polite rejection message; event is logged at `warn` level.

### RM-132 Bot + bridge deployment
- `adapters/telegram_bot/compose.yaml` — Docker Compose: `fastapi-bridge` + optional
  `job_ftch-mcp` services sharing a configs volume.
- Webhook mode (production): `POST /webhook/telegram` registered via
  `setWebhook` Telegram API call on startup.
- Polling mode (dev): long-polling fallback when `TELEGRAM_WEBHOOK_URL` is not set;
  no public HTTPS needed for local development.
- `docs/telegram_bot/deploy.md`: local dev, VPS (nginx + certbot), Docker Compose.

### RM-133 End-to-end bot integration test
- Fake Telegram Bot API server (recorded fixtures).
- Send `/run ai_jobs` update → assert FastAPI bridge calls `TenantRunner` →
  assert bot replies with `RunSummary` message.
- Send `/search "machine learning"` → assert search endpoint called →
  assert job cards returned as inline keyboard.

## Phase 26. Observability, lineage, and unified watermark  ‹PARALLEL-OK›

Purpose: production-grade operational visibility — metrics dashboards, job lineage tracing,
and an efficient unified incremental fetch primitive that replaces scattered per-source
`last_cursor`/`etag` implementations.

### RM-141 Unified IncrementalCursor primitive
- `IncrementalCursor` in `job_ftch/application/watermark.py`:
  `get(source_id: str) -> str | None`,
  `set(source_id: str, cursor: str) -> None`,
  `reset(source_id: str) -> None`.
- Backed by `StoreConnector` key `{tenant_id}:{source_id}:cursor`.
- Current rollout state: landed and already used by `RSSFeedSource` and
  `OfficialAPISource` derivatives. Remaining work is migration of any residual
  source-specific cursor state to the same primitive.
- All sources that currently scatter `last_cursor`, `last_seen_id`, `etag` fields
  in their own state migrate to `IncrementalCursor`. One store key pattern, everywhere.
- `SourceSpec` gains `incremental: bool = True`; when `False`, full re-fetch every run.
- `OfficialAPISource` (Phase 19), `RSSFeedSource` (Phase 21), and `PostgreSQLStore`
  all use `IncrementalCursor` via injection. No source manages its own cursor state.
- Integration test: two runs with same fixture; second run fetches 0 items (cursor held).

### RM-142 Job lineage graph
- `JobLineage` record in `domain/lineage.py`:
  `raw_item_id: str`, `job_id: str`, `group_id: str | None`,
  `pipeline_run_id: str`, `tenant_id: str`, `source_kind: str`, `source_name: str`,
  `extracted_at: datetime`, `stage_trace: list[str]`.
- Pragmatic rollout path landed first: build `JobLineage` on demand from persisted
  `JobRecord` + `JobGroup`, with `source_run_id` already injected into `JobRecord.metadata`
  during `Pipeline.run()`. This exposes lineage without a dedicated lineage store migration.
- Follow-up step: optional `LineageStore` protocol + backend if persisted per-stage lineage
  becomes necessary later.
- `raw_item_id` already on `JobRecord`; this is now wired to `job_id` in the lineage payload.
- CLI: `job_ftch tenants lineage <tenant_id> <job_id>` — shows origin raw item, source,
  run id, and stages.
- MCP tool: `get_job_lineage(job_id: str) -> JobLineage`.
- This is the "dbt lineage" equivalent: every output job is traceable to its raw source.

### RM-143 Prometheus metrics export
- `MetricsExporter` in `job_ftch/infrastructure/metrics/prometheus.py`.
- Metrics emitted after each `Pipeline.run()`:
  - `job_ftch_items_fetched_total{tenant_id, source_kind}` (counter)
  - `job_ftch_items_extracted_total{tenant_id, source_kind}` (counter)
  - `job_ftch_items_dropped_total{tenant_id, source_kind, reason}` (counter)
  - `job_ftch_items_failed_total{tenant_id, source_kind}` (counter)
  - `job_ftch_run_duration_seconds{tenant_id}` (histogram)
  - `job_ftch_jobs_delisted_total{tenant_id, source_kind}` (counter)
  - `job_ftch_job_groups_total{tenant_id}` (gauge, from `JobGroupStore.count()`)
- Current rollout state: landed behind `metrics_enabled` / `metrics_port`, with
  counters for fetched/extracted/dropped/failed, a run-duration histogram, and
  a job-group-total gauge. Delisted counters and richer per-daemon serving remain
  follow-up work.
- `prometheus_client` added to `[metrics]` extras group.
- Configurable: `metrics_enabled: bool = False`, `metrics_port: int = 9090`.
- In daemon mode (`--daemon`), metrics endpoint starts as a background aiohttp server.
- `RunSummary` history: last N summaries persisted in `PostgreSQLStore` as JSONB
  under `{tenant_id}:run_history`; CLI `job_ftch runs list` reads them.

### RM-144 RunSummary persistence and history
- `PostgreSQLStore.save_run_summary(summary: RunSummary) -> None` called at end of
  each `Pipeline.run()`.
- Schema: `jf_run_history(run_id PK, tenant_id, started_at, finished_at, summary JSONB)`.
- CLI: `job_ftch runs list [--tenant <id>] [--limit 20]` — tabular history.
- CLI: `job_ftch runs show <run_id>` — full `RunSummary` JSON.
- MCP tool: `list_runs(tenant_id: str, limit: int) -> list[RunSummary]`.

## Parallel work streams

### Stream A. Telegram
- Channels, groups, comments (polling) — done.
- Realtime event listener (Phase 21 RM-106).
- Telegram bot client UI (Phase 25).

### Stream B. Career sites
- Declarative HTML (done) + OfficialAPISource (Phase 19).
- Browser source for JS-rendered / CF-protected (Phase 20).
- Hard scraper for complex sites (Phase 20).

### Stream C. Cross-cutting quality
- Sanitize, validation, dedup, extraction, scoring, sinks, resilience — done.
- Filter profiles + configurable relevance (Phase 12).
- Search layer (Phase 16).
- Bypass strategies (Phase 18).
- Job lifecycle tracking (Phase 13).
- Company canonicalization (Phase 13).
- Cross-source aggregation (Phase 14).

### Stream D. Platform adapters
- FastStream, Dagster, MCP, FastAPI (Phase 22).
- FastMCP universal server (Phase 24).
- Telegram bot + FastAPI bridge (Phase 25).

### Stream E. Multi-tenant operations
- TenantConfig, isolated store/sink, multi-instance safety (Phase 23).

### Stream F. Observability
- Prometheus metrics, lineage graph, unified watermark, run history (Phase 26).

## Milestone boundaries

- `M1–M10` — MVP spine through release contour — **DONE**
- `M11 - Multi-source` — RM-063 to RM-067b
- `M12 - Configurable filter profiles` — RM-074 to RM-078
- `M13 - Domain model hardening` — RM-134 to RM-136
- `M14 - Cross-source job aggregation` — RM-137 to RM-140
- `M15 - Persistent store` — RM-068 to RM-073
- `M16 - Fulltext and semantic search layer` — RM-079 to RM-085
- `M17 - Scheduler and daemon mode` — RM-086 to RM-091
- `M18 - Source configuration system v2` — RM-092 to RM-096
- `M19 - Official API sources` — RM-097 to RM-101
- `M20 - Browser and hard scraper sources` — RM-102 to RM-105
- `M21 - Realtime and push ingestion` — RM-106 to RM-109
- `M22 - Library packaging and runtime adapters` — RM-110 to RM-114
- `M23 - Multi-tenant and multi-instance` — RM-115 to RM-120
- `M24 - FastMCP server` — RM-121 to RM-127
- `M25 - Telegram bot client + FastAPI bridge` — RM-128 to RM-133
- `M26 - Observability, lineage, and unified watermark` — RM-141 to RM-144
