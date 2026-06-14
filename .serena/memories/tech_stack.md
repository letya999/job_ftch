# Tech Stack

- Language/runtime: Python 3.12+, asyncio.
- Package/env manager: `uv`. Always run tests as `uv run pytest`, not `python -m pytest` (system Python lacks dev deps).
- Build backend: `hatchling`. Wheel includes only `job_ftch` package. Root `adapters/` requires PYTHONPATH=/app.
- Config: `pydantic-settings`, `.env` via `config.py`.
- Core deps declared in `pyproject.toml`:
  `pydantic`, `pydantic-settings`, `httpx`, `selectolax`, `rapidfuzz`, `openai`, `instructor`,
  `opentelemetry-api`, `opentelemetry-sdk`, `structlog`, `defusedxml`, `slowapi`.
- Optional/Feature deps (declared in pyproject optional groups):
  `aiosqlite`, `asyncpg`, `qdrant-client`, `pgvector`, `sentence-transformers`, `torch`.
  `feedparser` in `[feeds]` group - required for RSSFeedSource. NOT in core deps.
  `playwright` optional for BrowserSource (checked at import time, raises ImportError if missing).
  `pypdf`, `python-docx`, `pdfminer.six` in `[resume]` group - document parsing for bot uploads.
  `aiogram>=3.7` in `[telegram]` group - required for adapters/telegram_bot/. Tests guard with pytest.importorskip("aiogram").
- NLP retrieval quality deps (all opt-in, added in MVP batch B/C):
  `[language]`: `lingua-language-detector>=2.0` — 72+ language detection including KZ; loads ~500MB models.
    Enabled via `LANGUAGE_DETECTION_ENABLED=true`.
  `[translation]`: `ctranslate2`, `sentencepiece`, `huggingface_hub` — CPU-fast RU/EN translation
    via Helsinki-NLP opus-mt. KZ not supported (supports() returns False, TranslationNode skips).
    Models in `infrastructure/language/translator.py`. HuggingFace SHAs pinned in `_MODEL_REVISIONS`:
      opus-mt-ru-en: fbd6dc73284f95536648512cc21d57f19191961a
      opus-mt-en-ru: bb09c99d180016eac6819df3dae68edb1690fdee
    Enabled via `TRANSLATION_ENABLED=true`.
  Reranker: `jinaai/jina-reranker-v2-base-multilingual` via `fastembed` dep. Enabled via `RERANKER_ENABLED=true`.
- Structlog: configured in `application/logging.py`. `_mask_sensitive` processor runs before JSONRenderer,
  masks fields: api_key, token, secret, password, dsn, auth_hash → "***".
- fastembed critical note: `intfloat/multilingual-e5-small` requires `"query: "` prefix for queries
  and `"passage: "` prefix for passages. `FastEmbedProvider.embed_query()` / `embed_passage()` handle this.
- Storage & Search:
  SQLite (FTS5) for local persistence/search.
  PostgreSQL (tsvector) for production persistence/search.
  Qdrant for vector storage.
  OpenAI / Ollama / SentenceTransformers for embeddings.
- Qdrant client API: use `query_points()` not deprecated `search()`.
  Returns object with `.points` attribute.
  `hashlib.md5(data, usedforsecurity=False)` required for bandit B324.
- Dev tooling: `ruff`, `mypy` (strict), `pytest`+`pytest-asyncio`+`syrupy`, `bandit`.
  bandit: 0 HIGH findings as of MVP commit. B615 (HF model download) suppressed with `# nosec B615`.
  S104 (0.0.0.0 bind in cli.py/webhook.py) - accepted for Docker.
- Packaging layout is flat package directories, not `src/`.
- Ranking: RRF (Reciprocal Rank Fusion) for merging FTS and Vector results.
- Auth providers: `EnvAuthProvider` (reads `JOB_FTCH_AUTH_{SOURCE_ID}_{KEY}` env vars),
  `FileAuthProvider` (lazy YAML load from secrets file).
- Scheduler: `application/scheduler.py` - asyncio event loop, SIGINT/SIGTERM, `--daemon` flag, PID file.
- `SourceSpecFactory = Callable[[Any, AuthProvider], object]` - 2-arg protocol.
  Classes with 3-arg `__init__` (spec, auth, store) must use module-level factory functions.
- Deploy: `docker-compose.yml` starts bot+SQLite by default. Scale with `--profile postgres`/`vector`.
  `docs/deploy.md` covers DigitalOcean droplet provisioning.
