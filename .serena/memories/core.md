# Core

- Read this first. Follow-up memories:
  `mem:tech_stack` for runtime and dependency choices.
  `mem:conventions` for architectural and coding invariants.
  `mem:architecture/master_plan` for the current target shape of the library-first ETL core.
  `mem:pipeline/funnel_strategy` for the intended high-signal multi-node matching and routing flow.
  `mem:suggested_commands` for day-to-day repo commands on Windows.
  `mem:task_completion` for required verification gates before closing coding work.
- Repo state: phases 1-21 complete + MVP batches A/B/C on branch `mvp-release`.
- Top-level source map:
  `domain/` pure models only. `JobGroup` is the aggregate root for search/persistence.
  `application/` pipeline engine, protocols, use cases, registry, and scheduler.
  `infrastructure/` adapters for Telegram/HTTP/store/LLM/embeddings/auth/ingest.
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
  `adapters/document_parser.py` parse_document() for PDF/DOCX/HTML/MD/TXT bot uploads.
  `adapters/language_detector.py` LinguaLanguageDetector implements LanguageDetectorPort.
  `adapters/translation.py` CTranslate2Translator implements TranslatorPort (RU/EN only).
  `infrastructure/llm/reranker_provider.py` JinaRerankerProvider implements CrossEncoderPort.
  `app.py` composition root; supports `pipeline` (with `--daemon` flag), `search`, `--status`.
  `config.py` pydantic-settings config with `.env` loading.
  `tests/` 435 passing tests covering all major subsystems.
- Architectural target is hexagonal with expanded ports:
  Source, Node, Sink, Store, JobPersistence, SearchBackend, EmbeddingProvider, VectorBackend.
  NLP ports added in MVP batch B/C: LanguageDetectorPort, TranslatorPort, CrossEncoderPort.
  All three are opt-in via config flags; no performance impact when disabled.
- Current target evolution goes beyond single-record extraction:
  compact payload family should converge toward `RawItem -> JobDraft -> JobRecord -> JobGroup`,
  with rich typed blocks inside stable payloads rather than many fragile top-level transition types.
- Matching and routing target is a multi-node funnel that separates:
  post type, relevance, risk, quality, and aggregation confidence.
- Stable invariants from project docs:
  `domain/` may import only stdlib and `pydantic`.
  `application/` must NOT import `infrastructure/` - resolved via named-backend registry (ADR-020).
  `SanitizeNode` must be first in any pipeline chain.
  no secrets in code; use `.env`.
  adding dependencies requires updating `docs/tech_stack.md`.
  nontrivial architecture changes require an ADR in `docs/adr/`.
- Important planning docs:
  `docs/architecture.md` captures the current target architecture and matching funnel.
  `docs/adr/024-canonical-job-contract-and-matching-funnel.md` captures the same direction as an ADR.
- ADRs present through 028; ADR-028 documents NLP retrieval quality decisions (language detection, translation, cross-encoder reranking).
- Source spec types (discriminated union in `domain/source_spec.py`):
  `telegram`, `declarative`, `career_site`, `local_fixture`, `rest_api`, `browser`,
  `rss_feed`, `telegram_realtime`, `webhook`, `websocket`.
