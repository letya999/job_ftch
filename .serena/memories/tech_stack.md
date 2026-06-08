# Tech Stack

- Language/runtime: Python 3.12+, asyncio.
- Package/env manager: `uv`.
- Build backend: `hatchling`.
- Config: `pydantic-settings`, `.env` via `config.py`.
- Core deps already declared in `pyproject.toml`:
  `pydantic`, `pydantic-settings`, `httpx`, `selectolax`, `telethon`, `rapidfuzz`, `openai`, `instructor`, `opentelemetry-api`, `opentelemetry-sdk`, `structlog`.
- Optional/Feature deps (declared in pyproject optional groups):
  `aiosqlite`, `asyncpg`, `qdrant-client`, `pgvector`, `sentence-transformers`, `torch`.
  `feedparser` in `[feeds]` group - required for RSSFeedSource. NOT in core deps.
  `playwright` optional for BrowserSource (checked at import time, raises ImportError if missing).
- Storage & Search:
  SQLite (FTS5) for local persistence/search.
  PostgreSQL (tsvector) for production persistence/search.
  Qdrant for vector storage.
  OpenAI / Ollama / SentenceTransformers for embeddings.
- Qdrant client API note: use `query_points()` not deprecated `search()`.
  Returns object with `.points` attribute (not a plain list).
  `hashlib.md5(data, usedforsecurity=False)` required for bandit B324.
- Dev tooling:
  `ruff` for lint + format.
  `mypy` in strict mode.
  `pytest`, `pytest-asyncio`, `pytest-cov`.
  `bandit`.
- Packaging layout is flat package directories, not `src/`.
- Wheel packages include `domain`, `application`, `infrastructure`, `nodes`, `sinks`.
- Ranking: RRF (Reciprocal Rank Fusion) for merging FTS and Vector results.
- Auth providers: `EnvAuthProvider` (reads `JOB_FTCH_AUTH_{SOURCE_ID}_{KEY}` env vars),
  `FileAuthProvider` (lazy YAML load from secrets file).
- Scheduler: `application/scheduler.py` `Scheduler` class - asyncio event loop, SIGINT/SIGTERM,
  `--daemon` flag on `pipeline` subcommand, PID file at `.runtime/.pid`.
- `SourceSpecFactory = Callable[[Any, AuthProvider], object]` - 2-arg protocol.
  Classes with 3-arg `__init__` (spec, auth, store) must use module-level factory functions,
  not direct `@register_source_v2` class decoration.
