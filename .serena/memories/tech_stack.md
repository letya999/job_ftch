<!-- Memory Metadata
Last updated: 2026-06-17
Last commit: f9fc8b8 fix(classifier): remove false-positive announcement tokens
Scope: .serena/memories/tech_stack.md
Area: CORE
-->

# job_ftch — Tech Stack

## Language & Runtime
- Python 3.12+ (strict), `requires-python = ">=3.12"`
- Build: hatchling (`pyproject.toml`), package manager: `uv`

## Core Dependencies (always installed)
- pydantic ≥2.7 (domain models, validation)
- pydantic-settings ≥2.3 (config)
- httpx ≥0.27 (async HTTP)
- selectolax ≥0.3 (HTML parsing, lxml-based)
- rapidfuzz ≥3.9 (fuzzy dedup)
- opentelemetry-api + opentelemetry-sdk ≥1.25 (tracing)
- structlog ≥24.2 (structured logging)
- defusedxml ≥0.7.1 (XML security)
- slowapi ≥0.1.9 (rate limiting)

## Optional Dependency Groups (extras)
- `[browser]`: playwright ≥1.45
- `[stealth]`: playwright-stealth, curl-cffi
- `[openai]`: openai ≥1.30, instructor ≥1.4
- `[telegram]`: telethon ≥1.36, aiogram ≥3.7
- `[dagster]`: dagster ≥1.8
- `[faststream]`: faststream ≥0.5
- `[api]`: fastapi ≥0.115, uvicorn ≥0.30
- `[mcp]`: fastmcp ≥2.4
- `[bot]`: aiogram, telethon, fastapi, uvicorn, aiosqlite, pypdf, python-docx, pdfminer.six, PyYAML
- `[metrics]`: prometheus-client ≥0.20
- `[fastembed]`: fastembed ≥0.3
- `[sqlite]`: aiosqlite ≥0.20
- `[postgres]`: asyncpg ≥0.31.0
- `[embeddings]`: sentence-transformers ≥3.0, torch ≥2.3
- `[qdrant]`: qdrant-client ≥1.18.0
- `[pgvector]`: asyncpg, pgvector ≥0.3
- `[ollama]`: httpx
- `[feeds]`: feedparser ≥6.0
- `[site_scrapers]`: jmespath ≥1.0, feedparser ≥6.0
- `[realtime]`: aiohttp ≥3.9, websockets ≥12.0
- `[resume]`: pypdf, python-docx, pdfminer.six
- `[language]`: lingua-language-detector ≥2.0
- `[translation]`: ctranslate2 ≥4.0, sentencepiece ≥0.1.99
- `[reranker]`: fastembed ≥0.3

## Dev Dependencies
- ruff ≥0.4 (lint + format)
- mypy ≥1.10 (strict type checking, `ignore_missing_imports = true`)
- pytest ≥8.2, pytest-asyncio ≥0.23, pytest-cov ≥5.0
- pytest-benchmark ≥4.0, hypothesis ≥6.100, syrupy ≥4.0
- bandit ≥1.7 (security lint)
- pre-commit ≥3.7

## Testing
- pytest with `asyncio_mode = "auto"`
- Markers: unit, integration, slow, llm, e2e, network, telegram, superjob
- Tests under `tests/`, organized by domain/app/infra/nodes/e2e
- Phase-numbered: `test_phaseN_*.py`

## Config
- YAML source configs in `config/`
- `.env.dev` / `.env.prod` for secrets (gitignored); selected via `JOB_FTCH_ENV` (dev default)
- `pydantic-settings` Settings class in `job_ftch/config.py`

## Known Technical Debt (Pipeline Orchestration)
- **Linear pipeline, no parallelism**: Independent nodes (e.g. `PostTypeClassificationNode` and `DedupNode`) run sequentially. At current volumes (~200 items/run) not a bottleneck, but limits throughput at scale.
- **`model_copy()` per node**: Pydantic `model_copy(update=...)` allocates a new object at every node. With ~20 nodes, each item is copied ~20 times. Noticeable at thousands of items per run.
- **`get_settings()` in `Pipeline.run()`**: Global state dependency inside the orchestrator. Settings is accessed via `get_settings()` singleton instead of being injected via DI. Complicates multi-tenant isolation within a single process.