# Core

- Read this first. Follow-up memories:
  `mem:tech_stack` for runtime and dependency choices.
  `mem:conventions` for architectural and coding invariants.
  `mem:suggested_commands` for day-to-day repo commands on Windows.
  `mem:task_completion` for required verification gates before closing coding work.
- Repo state: pre-alpha scaffold. Docs are ahead of implementation.
- Top-level source map:
  `domain/` pure models only.
  `application/` pipeline engine, protocols, use cases.
  `infrastructure/` adapters for Telegram/HTTP/store/LLM.
  `nodes/` processing steps implementing Node protocol.
  `sinks/` output adapters implementing Sink protocol.
  `app.py` composition root entrypoint.
  `config.py` pydantic-settings config with `.env` loading.
  `tests/` currently smoke/import checks, not behavior coverage.
- Architectural target is hexagonal with 5 ports: Source, Node, Sink, Store, LLMProvider.
- Stable invariants from project docs:
  `domain/` may import only stdlib and `pydantic`.
  `SanitizeNode` must be first in any pipeline chain.
  no secrets in code; use `.env`.
  adding dependencies requires updating `docs/tech_stack.md`.
  nontrivial architecture changes require an ADR in `docs/adr/`.
