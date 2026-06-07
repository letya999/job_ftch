# Tech Stack

- Language/runtime: Python 3.12+, asyncio.
- Package/env manager: `uv`.
- Build backend: `hatchling`.
- Config: `pydantic-settings`, `.env` via `config.py`.
- Core deps already declared in `pyproject.toml`:
  `pydantic`, `pydantic-settings`, `httpx`, `selectolax`, `telethon`, `rapidfuzz`, `openai`, `instructor`, `opentelemetry-api`, `opentelemetry-sdk`, `structlog`.
- Optional/Feature deps:
  `aiosqlite`, `asyncpg`, `qdrant-client`, `pgvector`, `sentence-transformers`, `torch`.
- Storage & Search:
  SQLite (FTS5) for local persistence/search.
  PostgreSQL (tsvector) for production persistence/search.
  Qdrant for vector storage.
  OpenAI / Ollama / SentenceTransformers for embeddings.
- Dev tooling:
  `ruff` for lint + format.
  `mypy` in strict mode.
  `pytest`, `pytest-asyncio`, `pytest-cov`.
  `bandit`.
- Packaging layout is flat package directories, not `src/`.
- Wheel packages include `domain`, `application`, `infrastructure`, `nodes`, `sinks`.
- Ranking: RRF (Reciprocal Rank Fusion) for merging FTS and Vector results.
