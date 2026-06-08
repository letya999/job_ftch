# Plan: Architecture Hardening for Universal Vacancy ETL

Date: 2026-06-06
Branch: main (clean)
Status: PLAN ONLY — nothing implemented yet

## 0. Purpose

Prevent architectural lock-in before Phases 5–15 and community contributions cement the
current hardcoded extension model. Target vision: self-hosted, configurable universal
vacancy ETL where sources/parsers/nodes/sinks are added via config or single-file plugins
(never by editing core), supporting type-changing pipelines (RawItem -> Job), a node library
(clean / search / match: fulltext, vector, AI), multiple outputs, and community AI-coding
without forking core.

## 1. Verified current state (audited 2026-06-06)

Re-audited the codebase. Several earlier findings are now RESOLVED and MUST NOT be redone:

RESOLVED (do not touch):
- Node contract migration DONE: `is_sanitize` boolean flag is gone. `contracts.py` defines
  `PipelineNode` / `SanitizingNode` / `ProcessingNode`. `Pipeline.__init__` takes
  `sanitize_node: SanitizingNode` + `nodes: Sequence[ProcessingNode]` separately
  (`application/pipeline.py:80-97`). `app.py:153-160` returns the typed tuple.
- Stats duplication DONE: `StatsBase` extracted; `SourceRunStats` and `RunSummary` inherit it
  (`application/pipeline.py:24-77`).
- Per-item fault isolation DONE: `try/except/finally` with `finalized` flag; `mark_processed`
  runs in `finally` (`application/pipeline.py:124-200`).
- Phase 4 dedup DONE: `domain/dedup.py`, `nodes/dedup.py`, `tests/test_dedup.py`,
  `DedupNode` wired in `app.py:159`. Store extended with `record_duplicate` / `list_dedup_keys`.
- ADRs 001–005 already exist. ADR-004 = pipeline-node-contracts-and-stats,
  ADR-005 = raw-item-identity-and-dedup. NEW ADRs MUST start at 006.

STILL OPEN (this plan addresses these):
- B1 Typed stages: `Pipeline[PipelineItem]` is still single-type; `sink: Sink[PipelineItem]`.
  `RawItem -> Job` (Phase 5 extraction) cannot be expressed. CONFIRMED.
- B2 Extension registry: `build_source` / `build_sink` / `build_store` are if/elif in
  `app.py:163-184`; `_select_parser` is hardcoded if/elif in
  `infrastructure/sources/career_site.py:280-288`. No plugin discovery. CONFIRMED.
- B3 Declarative extraction: one Python parser class per site (`_GreenhouseParser`,
  `_BCCParser`). Does not scale to "20 sites + hh". CONFIRMED.
- B4 Sink fan-out / routing: exactly one sink (+ optional quarantine). No multi-sink, no
  conditional routing. CONFIRMED.
- E1 `JsonFileSink` O(n^2): non-JSONL mode rewrites the whole buffer to disk on every `emit`
  (`sinks/json_file.py:27-31`). CONFIRMED.
- E2 N+1 HTTP in `_BCCParser`: sequential per-vacancy detail fetches, no gather. CONFIRMED.
- H1 Private defaults in core: `career_site_allowed_hosts` defaults to `bcc.kz` / `www.bcc.kz`
  in `config.py:56-60`; `Settings()` instantiated at module import (`config.py:126`). CONFIRMED.

## 2. Industry patterns adopted (research-backed)

- dlt `@dlt.source` / `@dlt.resource`: source = decorated function; contributor adds ONE file.
  -> Decorator registry `@register_source` for custom adapters.
- dlt `rest_api` RESTAPIConfig: declarative dict-driven extraction (endpoints, pagination,
  field mapping), no per-site code.
  -> Declarative `CareerSiteConfig` executed by one generic source (B3).
- Python Packaging entry points (`importlib.metadata.entry_points(group=...)`): recommended
  for third-party plugins without forking.
  -> `[project.entry-points."job_ftch.sources"]` discovery for external packages (B2).
- Registry pattern with decorators (Open/Closed, O(1) dict lookup). Caveat: decorators only
  run on import -> need explicit builtin-module loading.
- Pydantic discriminated unions (`Field(discriminator='kind')`) for `SourceConfig`.
- Typed pipeline stages `Generic[In, Out]` composition for type-changing pipelines (B1).

Sources: packaging.python.org plugins guide; dev.to registry pattern; dlthub source &
rest_api docs; pydantic discriminated unions; type-safe pipeline articles.

## 3. New ADRs to create (docs/adr/, numbering continues at 006)

### ADR-006 Typed pipeline stages (addresses B1)
- Problem: single-type `Pipeline[PipelineItem]` blocks `RawItem -> Job` and `Job`-typed
  match/search nodes (Phase 14).
- Decision: introduce `Stage[In, Out]` Protocol (`async process(In) -> Out | None`).
  `Pipeline` composes typed stages; `ExtractNode: Stage[RawItem, Job]` is the type-change point.
  `SanitizingNode`/`ProcessingNode` become specializations of `Stage[RawItem, RawItem]`.
- Alternatives recorded: union type (reject — loses type safety); two sub-pipelines via store
  (fallback).
- Migration: keep `Pipeline[RawItem]` behavior identical until extraction lands; stages are a
  superset of the current node contract.

### ADR-007 Extension registry + plugin discovery (addresses B2)
- Problem: if/elif in `app.py` and `_select_parser`; no third-party extensibility.
- Decision (two-tier, dlt-style):
  1. Builtin registry via decorators in `application/registry.py`: `@register_source(kind)`,
     `@register_parser(matches=...)`, `@register_sink(kind)`, `@register_store(kind)`.
     Builtin modules explicitly imported once to trigger registration.
  2. Third-party via entry points: `importlib.metadata.entry_points(group="job_ftch.sources")`
     (and `.parsers`, `.sinks`, `.stores`).
- `SourceBackend`/`SinkBackend`/`StoreBackend` enums become open registry string keys.
- `build_source/sink/store` and `_select_parser` reduced to registry lookups.

### ADR-008 Declarative config-driven extraction (addresses B3)
- Problem: one Python class per career site.
- Decision: `CareerSiteConfig` (Pydantic) declaring list/detail selectors, field->CSS map,
  pagination, host allowlist. One `DeclarativeCareerSiteSource` executes config. Python parser
  only when declarative is insufficient (registered via ADR-007).
- "Add a site" becomes a YAML entry, not code.

### ADR-009 Sink fan-out & conditional routing (addresses B4)
- Problem: single sink; vision needs DB + Telegram + JSON simultaneously and routing
  (AI-role -> posting, borderline -> review, junk -> quarantine).
- Decision: `FanOutSink(sinks=[...])` + `RoutingSink(predicate -> sink)`. `Pipeline` accepts
  `sink: Sink | Sequence[Sink]`.

### ADR-001 amendment
- Update ADR-001 text: it still lists "5 Protocols: Source, Node, Sink, Store, LLMProvider".
  `Node` is now `PipelineNode`/`SanitizingNode`/`ProcessingNode`. Add a note referencing
  ADR-004 (contracts) and ADR-006 (stages).

## 4. Code changes (sequence respects dependencies)

Order matters; each step ends in passing tests + mypy + ruff.

1. `application/registry.py` (new): registry dicts + decorators + entry-point discovery
   (ADR-007). Add `pyproject.toml` entry-point group declarations (empty by default).
2. Refactor composition to registry: `app.py` `build_source/sink/store` -> lookups;
   `career_site.py` `_select_parser` -> parser registry. Register existing builtins
   (`telegram_*`, `career_site`, `local_fixture`, `json_file`, `memory`, `greenhouse`, `bcc`).
3. `Stage[In, Out]` in `application/contracts.py`; adapt `Pipeline` to typed stages while
   preserving current `Pipeline[RawItem]` runtime behavior (ADR-006). Update `tests/test_contracts.py`.
4. `CareerSiteConfig` + `DeclarativeCareerSiteSource` (`infrastructure/sources/declarative.py`)
   (ADR-008). Port Greenhouse to a declarative config as proof; keep class parser as fallback.
5. `sinks/fanout.py` + `sinks/routing.py`; `Pipeline` accepts sink sequence (ADR-009).
6. Efficiency: `JsonFileSink` writes once on finalize/flush (fix E1); `_BCCParser` detail
   fetches via `asyncio.gather` with bounded concurrency (fix E2).
7. Hygiene: remove `bcc.kz` defaults from `config.py` (empty default, document in `.env`);
   replace module-level `settings = Settings()` with a factory `get_settings()` (fix H1).

Files created: `application/registry.py`, `infrastructure/sources/declarative.py`,
`sinks/fanout.py`, `sinks/routing.py`, `docs/adr/006..009-*.md`, tests for each.
Files modified: `app.py`, `application/contracts.py`, `application/pipeline.py`,
`infrastructure/sources/career_site.py`, `infrastructure/sources/__init__.py`,
`sinks/json_file.py`, `sinks/__init__.py`, `config.py`, `docs/adr/001-*.md`,
`tests/test_contracts.py`, `tests/test_sources.py`, `pyproject.toml`.

## 5. Roadmap changes (docs/roadmap.md)

- Insert NEW "Phase 4.5 Architecture hardening" AFTER Phase 4 (dedup is done), BEFORE Phase 5
  (extraction depends on typed stages):
  - RM-026a Extension registry + plugin discovery (ADR-007)
  - RM-026b Typed stages Stage[In,Out] (ADR-006)
  - RM-026c Declarative CareerSiteConfig source (ADR-008)
  - RM-026d Sink fan-out / routing (ADR-009)
  - RM-026e Efficiency: JsonFileSink O(n^2) + BCC N+1 (E1, E2)
  - RM-026f Hygiene: remove private defaults, Settings factory (H1)
- Phase 5 (RM-028/RM-029): add "Blocked by: RM-026b (typed stages)".
- Phase 7 (RM-040/041/042): add "Depends on: RM-026d (fan-out)".
- Phase 11 (RM-063): rewrite to `SourceConfig` discriminated union + reference ADR-007;
  add RM-063a declarative `CareerSiteConfig`; add RM-067a entry-point discovery.
- Phase 14 (RM-077..081): note match/search nodes implemented as `Stage[Job, Job]` per ADR-006.
- Add top-of-file "Architecture principles" section: new source/node/sink = registry or config,
  never core edits; type change only via `Stage[In,Out]`; links to ADR-006..009.
- Update milestone list: add "M4.5 - Architecture hardening".

## 6. Rules changes

### AGENTS.md
- Rewrite "Extending" table: new source = (1) declarative `CareerSiteConfig`, or
  (2) `@register_source` single file, or (3) third-party entry-point package. Never edit
  `app.py` / `_select_parser`.
- Add to "Hard rules": type change only via `Stage[In,Out]` (no isinstance/union); new adapter
  must self-register (no if/elif by adapter type in core); sinks must not rewrite the whole file
  per emit.
- Add to "Never add": hardcoded domain-specific hosts/parsers in `config.py` or core.

### docs/rules.md
- Add "Adding a source" checklist: declarative? -> config; custom? -> decorator + contract test
  (`tests/test_contracts.py` as mandatory gate, ties to RM-056).

## 7. GitHub issues plan (letya999/job_ftch) — create only on explicit go

CREATE NEW:
- 4 ADR issues (labels `architecture`, `adr`): ADR-006 typed stages, ADR-007 registry+discovery,
  ADR-008 declarative extraction, ADR-009 sink fan-out — bodies from section 3.
- New label `phase-4.5` (color `#b60205`). Issues RM-026a..RM-026f from section 5.
- RM-063a (declarative CareerSiteConfig), RM-067a (entry-point discovery), under phase-11.

MODIFY EXISTING (if already created):
- RM-063: reword to discriminated union + ADR-007 link.
- RM-028 / RM-029: add "Blocked by RM-026b".
- RM-039: add explicit "fix O(n^2) full-file rewrite".
- RM-034, RM-077..081: add "implement as Stage[Job,Job] per ADR-006".
- RM-040/041/042: add "Depends on RM-026d".

LINKS / MILESTONES:
- Use "Blocked by #N" in bodies. Create milestone "M4.5 Architecture hardening"; shift later
  milestones.

## 8. Validation gates (every step)

- `ruff check` + `ruff format --check` clean.
- `mypy` clean (note: previously `Node` import was stale; now fixed — keep it clean).
- `pytest` green, including new tests per created module.
- No new dependency without updating `docs/tech_stack.md` (entry points use stdlib
  `importlib.metadata`; declarative source reuses `selectolax`; no new deps expected).

## 9. Out of scope (explicitly not now)

- Persistent SQLite store (Phase 12), search layer (Phase 14), scheduler (Phase 15) — unchanged
  by this plan except for the typed-stage / registry notes above.
- Actual LLM extraction implementation (Phase 5) — this plan only unblocks it.
