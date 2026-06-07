# Core

- Read this first. Follow-up memories:
  `mem:tech_stack` for runtime and dependency choices.
  `mem:conventions` for architectural and coding invariants.
  `mem:suggested_commands` for day-to-day repo commands on Windows.
  `mem:task_completion` for required verification gates before closing coding work.
- Repo state: pre-alpha scaffold. Docs are ahead of implementation.
- Top-level source map:
  `domain/` pure models only. `JobGroup` is the aggregate root for search/persistence.
  `application/` pipeline engine, protocols, use cases, and registry.
  `infrastructure/` adapters for Telegram/HTTP/store/LLM/embeddings.
  `infrastructure/backends/jobs/` persistence for Jobs and Groups.
  `infrastructure/backends/search/` Hybrid search (FTS + Vector) with RRF.
  `nodes/` processing steps implementing Node protocol (added `EmbeddingNode`).
  `sinks/` output adapters implementing Sink protocol.
  `app.py` composition root entrypoint, supports `pipeline` and `search` subcommands.
  `config.py` pydantic-settings config with `.env` loading.
  `tests/` comprehensive behavior coverage for core components.
- Architectural target is hexagonal with expanded ports: Source, Node, Sink, Store, JobPersistence, SearchBackend, EmbeddingProvider, VectorBackend.
- Stable invariants from project docs:
  `domain/` may import only stdlib and `pydantic`.
  `SanitizeNode` must be first in any pipeline chain.
  no secrets in code; use `.env`.
  adding dependencies requires updating `docs/tech_stack.md`.
  nontrivial architecture changes require an ADR in `docs/adr/`.
