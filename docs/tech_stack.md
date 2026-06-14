# Технологический стек job_ftch

Таблица показывает финальный стек (после реализации всего роадмапа). Столбец "Фаза" указывает, когда зависимость вводится. Тяжёлые опциональные зависимости — всегда в группах extras, никогда в `[project.dependencies]`.

---

## Язык и рантайм

| Инструмент | Версия | Назначение |
|---|---|---|
| Python | 3.12+ | Основной язык |
| asyncio | stdlib | Асинхронное выполнение |
| uv | latest | Управление зависимостями и venv |

---

## Ядро (обязательные зависимости)

| Библиотека | Фаза | Назначение |
|---|---|---|
| `pydantic >= 2.7` | 0 | Модели данных, валидация, SourceSpec discriminated unions |
| `pydantic-settings` | 0 | `Settings` из env / .env |
| `httpx` | 0 | Асинхронный HTTP-клиент для career sites и API |
| `selectolax` | 0 | Быстрый парсинг HTML (без lxml) |
| `rapidfuzz` | 0 | Нечёткое сравнение строк для дедупликации |
| `structlog` | 0 | Структурированное JSON-логирование |
| `opentelemetry-api` | 0 | Трейсинг без привязки к вендору |
| `opentelemetry-sdk` | 0 | SDK трейсинга |
| `defusedxml >= 0.7.1` | 0 | Безопасный XML парсинг (Personio, RSS boards, Sitemap) |

---

## LLM и извлечение

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `openai` | `[openai]` | 0 | OpenAI API клиент |
| `instructor` | `[openai]` | 0 | Структурированное извлечение (RawItem → JobDraft) через LLM |
| `fastembed` | `[fastembed]` | MVP | Локальные мультиязычные ONNX-эмбеддинги без GPU; включает `TextCrossEncoder` для реранкинга |
| `sentence-transformers` | `[embeddings]` | 14 | Локальные эмбеддинги для семантического поиска |

**Важно для fastembed**: модель `intfloat/multilingual-e5-small` требует префиксов `"query: "` для запросов и `"passage: "` для документов. Без них качество поиска падает на 10-15%. `FastEmbedProvider.embed_query()` / `embed_passage()` добавляют префиксы автоматически.

---

## NLP качество поиска (opt-in, MVP batch B/C)

| Библиотека | Extras group | Назначение |
|---|---|---|
| `lingua-language-detector>=2.0` | `[language]` | Определение языка вакансий (72+ языка, включая KZ). Модели ~500MB при первом запуске. |
| `ctranslate2` | `[translation]` | CPU-быстрый машинный перевод RU↔EN через Helsinki-NLP opus-mt |
| `sentencepiece` | `[translation]` | Токенизатор для opus-mt |
| `huggingface_hub` | `[translation]` | Скачивание моделей перевода (~300MB в `.runtime/translation_models/`) |
| `jinaai/jina-reranker-v2-base-multilingual` | через `fastembed` | Cross-encoder реранкинг (278M, 100+ языков, ~200-500ms CPU) |

KZ: определяется lingua (`kk→kz`), перевод не поддерживается (нет opus-mt модели) — `TranslatorPort.supports()` возвращает False, пропускается молча. Векторный поиск работает кросс-лингвально нативно.

---

## Источники данных

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `telethon` | `[telegram]` | 1 | Telegram MTProto-клиент |
| `playwright` | `[browser]` | 18 | Безголовый браузер для защищённых сайтов |
| `Pillow` + `pytesseract` | `[captcha]` | 16 | Базовое распознавание CAPTCHA |

---

## Хранилища

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `aiosqlite` | `[sqlite]` | 15 | SQLiteStore и SQLiteJobBackend (dev / self-hosted) |
| `asyncpg` | `[postgres]` | 15 | PostgreSQL без ORM (быстрее SQLAlchemy в async) |
| `qdrant-client` | `[qdrant]` | 14 | VectorBackend: индексация и запросы эмбеддингов |

SQLAlchemy не используется: `asyncpg` на прямых запросах быстрее, проще тестировать, нет N+1 скрытых запросов.

---

## Планировщик

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| — | — | 17 | Чистый `asyncio` (`application/scheduler.py`). APScheduler не используется (RM-087). |

---

## Обход защиты

| Библиотека / сервис | Extras group | Фаза | Назначение |
|---|---|---|---|
| ScrapeOps / Apify | настраивается | 16 | Управляемые облачные скраперы (ManagedScraperBypass) |

---

## Сообщения и события

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `nats.py` | `[nats]` | 27 | NATSTarget для рассылки job-событий |
| `aiokafka` | `[kafka]` | 27 | KafkaTarget для рассылки job-событий |
| `faststream` | `[faststream]` | 20 | Обёртка пайплайна как воркер очереди сообщений |
| `dagster` | `[dagster]` | 22 | Runtime adapter: assets/materializations поверх `Pipeline.run()` |

---

## MCP и API

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `fastmcp` | `[mcp]` | 22 | FastMCP-сервер: stdio + SSE/HTTP транспорты |
| `fastapi` | `[api]` | 23 | FastAPI-мост для Telegram webhook |
| `uvicorn` | `[api]` | 23 | ASGI-сервер для FastAPI |

---

## Telegram-бот

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| — | — | 23 | Telegram Bot API потребляется напрямую через `httpx`. Фреймворки (aiogram и др.) не используются. |

---

## Наблюдаемость (производство)

| Библиотека | Extras group | Фаза | Назначение |
|---|---|---|---|
| `prometheus-client` | `[metrics]` | 26 | Экспорт метрик (jobs_fetched, jobs_failed и др.) |
| `opentelemetry-exporter-otlp` | `[otel]` | 26 | Экспорт трейсов в Jaeger / Tempo |

---

## Инструменты разработки (dev-зависимости)

| Инструмент | Назначение |
|---|---|
| `ruff` | Линтинг + форматирование (заменяет flake8, black, isort) |
| `mypy` | Статическая проверка типов |
| `pytest` + `pytest-asyncio` | Тестирование |
| `bandit` | Поиск уязвимостей |
| `coverage` | Покрытие тестами |

---

## Почему НЕ используются некоторые инструменты

| Инструмент | Причина отказа |
|---|---|
| Scrapy | Не нативный asyncio; собственный цикл событий конфликтует с нашим |
| LangChain | Избыточная абстракция, vendor lock-in, скрывает что именно делает LLM-вызов |
| SQLAlchemy | Накладные расходы ORM не нужны; asyncpg на прямых запросах быстрее и прозрачнее |
| Celery | Избыточен; APScheduler + asyncio задачи достаточны на начальных этапах |
| BeautifulSoup | selectolax в 5-10x быстрее при парсинге HTML |
| requests / aiohttp | httpx предоставляет синхронный + асинхронный API, лучшее соответствие типов |
