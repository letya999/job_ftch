<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: job_ftch/
Area: CORE
-->

# job_ftch — Core Source Map

## What
Open-source async pipeline: Telegram channels/groups/comments + career sites → structured JSON vacancies.
Hexagonal architecture (Ports & Adapters), Python 3.12+, async-native.

## Current relevance experiment state
- Read `mem:job_ftch/legacy_pipeline_experiments_2026_07_13` before changing legacy YAML eval graphs, combined extraction/relevance prompts, or interpreting H11–H17 metrics.

## Top-level layout
- `job_ftch/` — main package
  - `domain/` — pure domain models, zero imports outside pydantic+stdlib. **Hard rule.**
  - `application/` — orchestration, contracts (protocols), builder, pipeline, scheduler, plugins. Imports only `domain/` + stdlib + pydantic.
  - `nodes/` — processing stages (sanitize, extraction, dedup, quality, etc.). No direct imports of `infrastructure/`/`adapters/`; freely imports `domain/`, `application/`, and computational/cross-cutting libs (`numpy`, `rapidfuzz`, `yaml`). Enforced by `scripts/check_module_boundaries.py`.
  - `sinks/` — output sinks (JSON file, telegram posting, fanout, buffering). Same convention as `nodes/`; not separately checked by the boundary script.
  - `infrastructure/` — adapters for external world (LLM, stores, sources, embeddings, bypass, auth). Can import everything.
  - `cli.py` — entry point, argparse subcommands: pipeline, runs, search, status, dedup. **Primary CLI entry** (`job_ftch.cli:main`).
  - `config.py` — Settings (pydantic-settings), source specs, render defaults.
- `config/` — YAML configs for sources (telegram channels, career sites), `.example` files.
- `docs/` — architecture, vision, rules, tech_stack, ADRs (28+ ADRs in `docs/adr/`).
- `tests/` — organized by domain/application/nodes/infrastructure/e2e. Phase-numbered test files.
- `scripts/` — auth_telethon, check_module_boundaries, e2e_probe, export_schema, live_probe, run_diagnostics.
- `adapters/` — top-level integration adapters (dagster, fastapi, faststream, mcp, telegram_bot). NOT in the wheel.
- `docker-compose.dev.yml` — local dev compose (replaces deleted `docker-compose.yml`).

## Key domain models
- `RawItem` → `SanitizeNode` → `JobDraft` → extraction nodes → `JobRecord` → sinks
- `Job` is the canonical enriched model; `JobRecord` is the flattened output record
- `JobDraft` is the intermediate between raw and enriched
- See `mem:tech_stack` for frameworks/libraries.

## Current Pipeline Node Order (from builder.py::build_nodes, as of e459822)
1. `SanitizeNode` — always first gate
2. `SnapshotFilterNode` — (opt-in, when `run_id` set) always 2nd
3. `SourceContextNode` — enrich source metadata
4. `GarbageFilterNode` — drop non-job garbage early
5. `PostTypeClassificationNode` — classify post type
6. `HardFilterNode` — profile-based hard filter
7. `DedupNode` — exact + fuzzy dedup
8. `BgeMThreeNode` | `EmbeddingPrefilterNode` — (opt-in, mutually exclusive) local embedding prefilter
9. `SemanticPrefilterNode` — token-overlap + embedding role-anchor
10. `ExtractionNode` — LLM-based extraction
11. `ExtractionValidationNode` — validate extraction output
12. `TitleCompanyNormalizationNode` — normalize title/company
13. `SkillNormalizationNode` — normalize skills
14. `LocationWorkModeNormalizationNode` — normalize location/work mode
15. `CompensationParsingNode` — parse compensation
16. `JobLifecycleNode` — lifecycle management
17. `JobAggregationNode` — group similar jobs
18. `LanguageDetectionNode` — (opt-in) detect language
19. `TranslationNode` — (opt-in) translate to target language
20. `EmbeddingNode` — (opt-in) vectorize for semantic search
21. `MultiProfileMatchNode` — match against user profiles
22. `ParallelScoringNode` — (opt-in, requires BGE-M3 + positive shots) shot-anchor scoring
23. `IsJobNode` — (opt-in) job/non-job classifier
24. `RiskScoringNode` — risk assessment
25. `QualityScoringNode` — quality assessment
26. `JobValidationNode` — final validation
27. `LLMRelevanceClassificationNode` — (opt-in) low-confidence LLM relevance gate
28. `PresentableTextNode` — (opt-in, gated by `_llm_supports_generate_text`) presentable text generation
29. `RoutingNode` — accept/review/reject routing
30. `_FinalGroupUpdateNode` — final scoring write-back to job group store

## 6 Core Protocols (in `application/contracts.py`)
1. `Source[T]` — `async fetch() → AsyncIterator[T]`
2. `Stage[In, Out]` — `async process(item: In) → Out | None`
3. `Sink[T]` — `async emit(item: T)`
4. `Store` — dedup state + run state persistence
5. `LLMProvider` — `extract[T](text, schema) → T`
6. `StoreConnector` — universal connector → `SQLStoreAdapter` → PostgreSQL

## Extension model
- Self-registration via `@register_source`, `@register_sink`, etc. — entry points in `pyproject.toml`.
- No `if/elif` dispatch by adapter kind in core.
- Plugins go in `job_ftch.sources`, `job_ftch.sinks`, etc.

## Hard rules
- `domain/` zero imports outside pydantic+stdlib — no exceptions.
- `SanitizeNode` always first in pipeline chain.
- Type changes only via `Stage[In, Out]`, no ad hoc isinstance/union routing.
- No credentials in code, `.env` only.
- New dependency → update `docs/tech_stack.md` first.
- Architecture change → write ADR in `docs/adr/` first.
- Sinks must not rewrite whole output file on every `emit`.
- Namespace: all importable code under `job_ftch` package.

## Entry points
- CLI: `job_ftch.cli:main` (installed as `job_ftch` command)
- MCP server: `adapters.mcp.server:create_server`

## New in post-MVP
- `nodes/snapshot_filter.py` — incremental source diff filtering
- `infrastructure/sources/site_parsers/` — site-specific parsers (yandex.py)
- `infrastructure/stores/migrations/002_source_snapshots*.sql` — snapshot table migrations
- `infrastructure/sources/site_fingerprinter.py` — site fingerprinting for auto-detection
