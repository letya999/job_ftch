---
title: "Backends (Бэкенды хранения вакансий)"
description: "Backends в проекте `job_ftch` — это собирательное название"
updated: 2026-07-24
---
# Backends (Бэкенды хранения вакансий)

## Что это такое

Backends в проекте `job_ftch` — это собирательное название
для группы интерфейсов (Protocols), которые отвечают за
долгосрочное хранение данных (вакансий) и предоставление их
для внешних сервисов (поиска, UI, API).
В отличие от [Store](store.md), который нужен только самому
пайплайну во время работы, Backends нужны *после* того, как
пайплайн завершился.

В проекте архитектурно нет одного гигантского "монолитного
хранилища".
Вместо этого есть четыре узкоспециализированных Protocol-а.

## Зачем это нужно и ПОЧЕМУ так устроено

Задачи хранения, полнотекстового поиска и векторного
(семантического) поиска требуют совершенно разных баз данных
и алгоритмов.
PostgreSQL хорош для хранения реляционных данных и
транзакций.
ElasticSearch идеален для полнотекстового поиска.
Qdrant или pgvector — для векторных эмбеддингов. 

Если бы мы связали всю систему с одной СУБД (например,
захардкодили PostgreSQL), мы бы лишили систему гибкости.
Разделение на независимые Protocol-ы позволяет комбинировать
хранилища (Mix-and-Match) в зависимости от бюджета,
инфраструктуры и нагрузки.

Например:
- *Self-hosted MVP (Zero Infra)*: SQLite (хранение) + SQLite FTS (поиск) + SentenceTransformers (локальные вектора). Никаких докеров, всё в одном файле.

- *Enterprise High Load*: PostgreSQL (хранение) + ElasticSearch (поиск) + Qdrant (вектора) + OpenAI (векторизация).

## Четыре независимых Protocol-а

В `contracts.py` описаны четыре бэкенд-сущности:

### 1. JobPersistenceBackend
**Задача**: Основное надежное хранилище (Source of Truth)
финальных `JobRecord`.
Поддерживает CRUD операции (Create, Read, Update, Delete).
Не используется для сложного поиска, только для извлечения
вакансий по ID или для пагинации.
**Реализации**:
- `SQLiteJobBackend`

- `PostgreSQLJobBackend`

```python class JobPersistenceBackend(Protocol):
    async def save(self, job: JobRecord) -> None: ...
    async def get_job(self, job_id: str) -> JobRecord | None: ...
    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]: ...
```

### 2. SearchBackend
**Задача**: Полнотекстовый поиск по базе вакансий на
естественном языке (по заголовкам, описаниям, навыкам).
Возвращает сгруппированные вакансии (`JobGroup`).
**Реализации**:
- `PostgreSQLFTSBackend` (использует встроенный tsvector + GIN индексы Postgres).

- `HybridSearchBackend` (комбинирует текстовый поиск и векторный).

```python class SearchBackend(Protocol):
    async def search(self, query: str, limit: int = 20) -> list[JobGroup]: ...
```

### 3. EmbeddingProvider
**Задача**: Превращение текста (слов) в числовые вектора
(embeddings) для семантического поиска.
Он только считает вектора, но не хранит их.
**Реализации**:
- `OpenAIEmbeddingProvider` (через API OpenAI, дорого, но качественно).

- `SentenceTransformersProvider` (считает вектора локально без API, бесплатно).

- `OllamaProvider` (через self-hosted LLM).

```python class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

### 4. VectorBackend
**Задача**: Хранение огромного массива числовых векторов
(которые выдал `EmbeddingProvider`) и сверхбыстрый поиск
"ближайших соседей" (Nearest Neighbor Search).
**Реализации**:
- `QdrantVectorBackend` (специализированная векторная БД).

- `PgVectorBackend` (расширение pgvector для PostgreSQL).

```python class VectorBackend(Protocol):
    async def upsert(self, job_id: str, vector: list[float], payload: dict) -> None: ...
    async def search(self, vector: list[float], limit: int, filter: dict) -> list[str]: ...
```

## Взаимосвязь при работе

Когда пайплайн успешно завершает обработку вакансии и
доходит до Sink-а (например, в API адаптере):
1. Вакансия сохраняется в `JobPersistenceBackend`.

2. Текст вакансии передается в `EmbeddingProvider` для генерации вектора.

3. Полученный вектор сохраняется в `VectorBackend` с привязкой к ID вакансии.

4. При поисковом запросе пользователя `SearchBackend` делает гибридный запрос (текстовый + векторный) и отдает лучшие результаты.

## Store vs JobPersistenceBackend (Напоминание)

- **Store**: Служебная БД для самого пайплайна. Хранит ключи дедупликации, курсоры, статус работы источников. Схема — "ключ-значение".

- **JobPersistenceBackend**: "Клиентская" БД. Хранит готовые вакансии. Схема — реляционная. Отдает данные наружу.

## Типичные ошибки и что нельзя делать

1. **Реализовывать сложный поиск (LIKE / ILIKE) внутри JobPersistenceBackend.**

`JobPersistenceBackend` предназначен только для точечных
выборок (CRUD).
Тяжелый поиск по тексту — это строгая ответственность
`SearchBackend`.

2. **Забывать об инвалидации векторов.**

Если вакансия обновляется или удаляется из
`JobPersistenceBackend`, соответствующий вектор также должен
быть обновлен или удален из `VectorBackend`.
Иначе в поиске появятся фантомные вакансии.

3. **Смешивать домены хранения.**

Никогда не сохраняйте `JobDraft` или `RawItem` в базу
`JobPersistenceBackend`.
Это хранилище предназначено исключительно для
валидированных, готовых `JobRecord`.

## Связи с другими сущностями

- [Sink](sink.md) — обычно `Sink` является "мостом", который перекладывает данные из пайплайна в `Backends`.

- [JobRecord](job_record.md) — основная сущность, сохраняемая в долгосрочную память.

- [Store](store.md) — полная противоположность Backend-ов по смыслу использования.
