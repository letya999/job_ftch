---
title: "Технологический стек"
description: "Актуальный стек зависимостей job_ftch по pyproject.toml: core dependencies, extras и запрещённые инструменты."
updated: 2026-08-03
---
# Технологический стек

Источник правды: `pyproject.toml`. Этот документ объясняет назначение
зависимостей и границы добавления новых библиотек.

## Runtime

| Инструмент | Роль |
|---|---|
| Python 3.12+ | Основной runtime |
| `uv` | Установка, venv, запуск команд |
| `asyncio` / `anyio` | Async execution и concurrency limiting |

## Core dependencies

| Библиотека | Зачем |
|---|---|
| `pydantic`, `pydantic-settings` | Domain/config models |
| `httpx` | HTTP ingest/API client |
| `selectolax`, `trafilatura` | HTML/main-text extraction |
| `rapidfuzz` | Dedup/string similarity |
| `structlog` | Structured logging |
| `opentelemetry-api`, `opentelemetry-sdk` | Telemetry boundary |
| `defusedxml` | Safe XML/RSS/sitemap parsing |
| `slowapi` | FastAPI/webhook rate limiting |
| `PyYAML` | Runtime/tenant/source YAML |
| `dateparser` | Multilingual/relative dates |
| `filelock` | Cross-process tenant locks |
| `psutil` | Browser child-process cleanup |

## Extras

| Extra | Основные зависимости | Назначение |
|---|---|---|
| `[openai]` | `openai`, `instructor` | LLM extraction/classification |
| `[telegram]` | `telethon`, `aiogram` | Telegram reader и bot API |
| `[bot]` | `aiogram`, `telethon`, `fastapi`, document parsers | Telegram bot runtime |
| `[api]` | `fastapi`, `uvicorn` | HTTP adapter |
| `[mcp]` | `fastmcp` | MCP server |
| `[local_mcp]` | `numpy` | Lightweight local MCP runtime required by builtin relevance extensions |
| `[faststream]` | `faststream` | Message-worker adapter |
| `[dagster]` | `dagster` | Dagster wrapper |
| `[sqlite]` | `aiosqlite` | Local/dev store |
| `[postgres]` | `asyncpg` | Production store |
| `[qdrant]` | `qdrant-client` | Vector backend |
| `[pgvector]` | `asyncpg`, `pgvector` | Postgres vector backend |
| `[fastembed]` | `fastembed`, `huggingface-hub` | Local embeddings/reranker |
| `[embeddings]` | `sentence-transformers`, `torch` | Local embedding providers |
| `[bgem3]` | `flagembedding`, `torch`, bounded HF stack | BGE-M3 dense/sparse experiments |
| `[reranker]` | `fastembed` | Cross-encoder reranking |
| `[language]` | `lingua-language-detector` | Language detection |
| `[translation]` | `ctranslate2`, `sentencepiece` | RU/EN translation path |
| `[feeds]` | `feedparser` | RSS feeds |
| `[site_scrapers]` | `jmespath`, `feedparser`, `parsel` | Monitors/scrapers |
| `[extraction]` | `trafilatura`, `extruct`, `lxml` | Structured extraction |
| `[realtime]` | `aiohttp`, `websockets` | Realtime/push variants |
| `[browser]` | `patchright`, `cloakbrowser` | Browser-backed scraping tiers |
| `[stealth]` | `playwright-stealth`, `curl-cffi==0.15.0` | TLS/browser impersonation |
| `[tls_client]` | `tls-client` | Alternative TLS/HTTP impersonation transport |
| `[camoufox]` | `camoufox` | Firefox anti-detect tier |
| `[nodriver]` | `nodriver` | CDP-native browser tier |
| `[parity_lab]` | `cryptography`, `httpx[http2]`, `hypercorn[h3]`, `maxminddb`, `starlette` | Local-only browser/network parity lab |
| `[tracing]` | OTLP HTTP exporter | OpenObserve/Langfuse export |
| `[langfuse]` | `langfuse` | Eval/trace client |
| `[resilience]` | `tenacity` | Explicit retry helpers |
| `[ollama]` | `httpx` | Ollama-compatible LLM path |

`[all]` объединяет shipped runtime optional stack. Local-only lab/tooling extras
such as `[parity_lab]` stay separate when they can conflict with production
adapter dependency ranges. Новые heavy зависимости добавлять только через
отдельный extra и с обновлением этого файла.

## Dev dependencies

- `ruff` — lint + format.
- `mypy` — static typing.
- `pytest`, `pytest-asyncio`, `pytest-cov` — tests/coverage.
- `bandit`, `pip-audit` — security/supply-chain checks.
- `hypothesis`, `syrupy`, `pytest-benchmark` — property/snapshot/benchmark tests.

## Явно не используем

| Инструмент | Причина |
|---|---|
| Scrapy | Чужой event loop и тяжёлая crawler-модель |
| SQLAlchemy/ORM | Stores используют прямой async SQL/driver layer |
| Celery | Текущий scheduler и runtime orchestration построены на asyncio |
| Kafka/Airflow | Не входят в текущую архитектуру и release contour |
| LangChain/LangGraph | Скрывают LLM boundary и усложняют воспроизводимость |
| BeautifulSoup | `selectolax` быстрее и уже покрывает нужный HTML parsing |
| FlareSolverr | Удалён из bypass architecture и не является fallback |

## Правило добавления зависимости

1. Проверить, нельзя ли решить stdlib/current stack.
2. Если dependency нужна, определить: core или extra.
3. Для core dependency обосновать, почему она нужна всем runtime paths.
4. Обновить `pyproject.toml`, этот документ и relevant docs/tests.
5. Для security-sensitive dependency добавить release/security validation.
