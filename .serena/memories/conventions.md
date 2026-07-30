<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: .serena/memories/conventions.md
Area: CORE
-->

# job_ftch — Conventions

## Code Style
- ruff with `target-version = "py312"`, `line-length = 100`
- Lint: E, F, I, UP, B, SIM, TCH (E501 ignored)
- `secrets = True` in Pydantic models for sensitive fields
- `model_config = ConfigDict(populate_by_name=True)` common pattern
- `from __future__ import annotations` at top of every module

## Naming
- Source adapters: `*Source` suffix (e.g. `TelegramChannelSource`, `CareerSiteSource`)
- Processing nodes: `*Node` suffix (e.g. `SanitizeNode`, `ExtractionNode`)
- Sinks: `*Sink` suffix (e.g. `JsonFileSink`, `TelegramPostingSink`)
- Store implementations: `*Store` suffix (e.g. `InMemoryStore`, `SQLiteStore`, `PostgreSQLStore`)
- Domain enums: `StrEnum` subclasses (e.g. `SourceKind`, `WorkMode`, `Seniority`)
- Phase-numbered test files: `test_phaseN_<topic>.py`

## Type Hints
- Strict mypy (`strict = true`, `ignore_missing_imports = true`)
- `Protocol` + `@runtime_checkable` for all port interfaces
- Generic protocols: `Source[T]`, `Stage[In, Out]`, `Sink[T]`
- `TypeVar` declarations in contracts module
- No bare `Any` except documented compromises
- Optional drivers use `try/except ImportError` + `None` assignment pattern (mypy overrides)

## Domain Models
- All Pydantic v2 `BaseModel`
- Models in `domain/` import only `pydantic` + stdlib — hard boundary
- Hashing: `_stable_hash(*parts)` for dedup keys (SHA-256 over normalized pipe-delimited string)
- Enums: `StrEnum` for all categorical fields
- `Job` → `JobDraft` → `JobRecord` conversion helpers in `domain/contracts.py`

## Async Patterns
- All I/O is async (asyncio, httpx, aiosqlite, asyncpg)
- `AsyncIterator` for `Source.fetch()`
- `async def process()` for all `Stage` implementations
- Pipeline orchestration in `application/pipeline.py`

## Pipeline Node Order
See `mem:core` for the full up-to-date node list from `builder.py::build_nodes`.
Summary: `SanitizeNode` (always first) → optional `SnapshotFilterNode` → context/garbage/post-type
filters → hard filter/dedup → optional embedding prefilter → semantic prefilter → extraction +
normalization → aggregation → optional language/translation/embedding → profile match → optional
parallel scoring → risk/quality/validation → optional LLM relevance + presentable text →
`RoutingNode` → `_FinalGroupUpdateNode` → sinks.

## Plugin Registration
- Decorator-based: `@register_source(name)`, `@register_sink(name)`, etc.
- Entry points in `pyproject.toml` under `job_ftch.sources`, `job_ftch.sinks`, etc.
- Factory functions take `(SourceSpec, AuthProvider)`, not monolithic `Settings`

## Config
- YAML in `config/` for source definitions
- `.env.dev` / `.env.prod` for secrets (gitignored); `JOB_FTCH_ENV` (`dev` default, `prod`/`production`)
  selects which pair `Settings._resolve_env_files()` loads alongside base `.env`. Mirrored by
  `Dockerfile.{dev,prod}` and `docker-compose.{dev,prod}.yml`.
- `CareerSiteConfig` for declarative sources, `@register_source` for programmatic ones