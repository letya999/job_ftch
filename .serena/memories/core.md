# Core

- Read this first. Follow-up memories:
  `mem:tech_stack` for runtime and dependency choices.
  `mem:conventions` for architectural and coding invariants.
  `mem:architecture/master_plan` for the current target shape of the library-first ETL core.
  `mem:pipeline/funnel_strategy` for the intended high-signal multi-node matching and routing flow.
  `mem:suggested_commands` for day-to-day repo commands on Windows.
  `mem:task_completion` for required verification gates before closing coding work.
  `mem:mvp_release` for MVP release state, deploy layout, and open backlog.

- Repo state: phases 1-28 complete + MVP release prep on branch `mvp-release`.
  Commit `7af8a54`: adapter restructure, deploy artifacts, security hardening (592 tests pass).

- Top-level source map:
  `domain/` pure models only. `JobGroup` is the aggregate root for search/persistence.
  `application/` pipeline engine, protocols, use cases, registry, scheduler, and helper modules:
    - `application/profile_inputs.py` candidate profile builder (moved from adapters/).
    - `application/source_inputs.py` source spec factory helpers (moved from adapters/).
    - `application/source_validator.py` source spec validation (moved from adapters/).
    - `application/logging.py` structlog setup with _mask_sensitive processor (api_key/token/secret/password/dsn masked).
  `infrastructure/` adapters for Telegram/HTTP/store/LLM/embeddings/auth/ingest.
    - `infrastructure/language/detector.py` LinguaLanguageDetector (moved from adapters/).
    - `infrastructure/language/translator.py` CTranslate2Translator (moved from adapters/); HuggingFace model SHAs pinned.
    - `infrastructure/document_parser.py` parse_document() for PDF/DOCX/HTML/MD/TXT (moved from adapters/).
    - `infrastructure/llm/reranker_provider.py` JinaRerankerProvider implements CrossEncoderPort.
  `infrastructure/backends/jobs/` persistence for Jobs and Groups.
  `infrastructure/backends/search/` Hybrid search (FTS + Vector) with RRF.
  `infrastructure/sources/api/` REST API sources (Greenhouse, HH.ru) via `OfficialAPISource`.
  `infrastructure/sources/browser/` Browser scraping stub (Playwright, optional).
  `infrastructure/sources/realtime/` RSS, Webhook (stub), WebSocket (stub).
  `infrastructure/sources/telegram_realtime.py` Telethon NewMessage realtime source.
  `infrastructure/auth/` `EnvAuthProvider` and `FileAuthProvider`.
  `infrastructure/ingest/polling.py` `PollingMode` ingest strategy.
  `nodes/` processing steps implementing Node protocol (includes `EmbeddingNode`).
  `nodes/language_detection.py` LanguageDetectionNode (opt-in via LANGUAGE_DETECTION_ENABLED).
  `nodes/translation.py` TranslationNode (opt-in via TRANSLATION_ENABLED).
  `sinks/` output adapters implementing Sink protocol.
  `sinks/buffering.py` BufferSink[T] generic in-memory sink for batch post-processing.
  `app.py` composition root; supports `pipeline` (with `--daemon` flag), `search`, `--status`.
  `config.py` pydantic-settings config with `.env` loading.

  ROOT ADAPTERS (runtime wrappers, NOT in the wheel - require PYTHONPATH=/app):
  `adapters/telegram_bot/` Telegram bot (aiogram3) - MVP deploy target.
    - handlers/, fsm/, filters/, keyboards/, middlewares/, config.py, main.py, api.py, bot.py, formatter.py
  `adapters/mcp/` MCP server (FastMCP).
  `adapters/fastapi/` FastAPI adapter (scaffold).
  `adapters/faststream/` FastStream adapter (scaffold).
  `adapters/dagster/` Dagster adapter (scaffold).
  Each has own Dockerfile, README, CI workflow (.github/workflows/adapter-*.yml).

  `docker-compose.yml` default: bot + SQLite (zero-infra). Profiles: `--profile postgres`, `--profile vector`.
  `docs/deploy.md` DigitalOcean droplet guide.
  `tests/` 592 passing tests (11 skipped for optional deps).

- Architectural target is hexagonal with expanded ports:
  Source, Node, Sink, Store, JobPersistence, SearchBackend, EmbeddingProvider, VectorBackend.
  NLP ports added in MVP batch B/C: LanguageDetectorPort, TranslatorPort, CrossEncoderPort.
  All three are opt-in via config flags; no performance impact when disabled.

- Current target evolution goes beyond single-record extraction:
  compact payload family should converge toward `RawItem -> JobDraft -> JobRecord -> JobGroup`,
  with rich typed blocks inside stable payloads rather than many fragile top-level transition types.

- Stable invariants from project docs:
  `domain/` may import only stdlib and `pydantic`.
  `application/` must NOT import `infrastructure/` - resolved via named-backend registry (ADR-020).
  EXCEPTION: builder, pipeline, tenant_runner, source_inputs are in `application_runtime_exception`
    set in `scripts/check_module_boundaries.py` (they legitimately import infra at runtime).
  `SanitizeNode` must be first in any pipeline chain.
  No secrets in code; use `.env`.
  Root `adapters/` is NOT in the wheel (hatchling packages=["job_ftch"]). Dockerfiles set PYTHONPATH=/app.
  Adding dependencies requires updating `docs/tech_stack.md`.
  Nontrivial architecture changes require an ADR in `docs/adr/`.

- ADRs present through 028; ADR-028 documents NLP retrieval quality decisions.
- Source spec types (discriminated union in `domain/source_spec.py`):
  `telegram`, `declarative`, `career_site`, `local_fixture`, `rest_api`, `browser`,
  `rss_feed`, `telegram_realtime`, `webhook`, `websocket`.
