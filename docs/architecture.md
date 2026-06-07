# Архитектура job_ftch

## Цель

Гексагональная архитектура (Ports & Adapters): доменная логика в центре, инфраструктурные детали снаружи. Система растёт вместе с роадмапом, не меняя форму ядра.

Визуальные диаграммы (C4 L1/L2/L3) и эволюционные слайды — в [README.md](../README.md).

---

## Принципы модульных границ

Правила жёсткие, без исключений:

| Слой | Разрешённые импорты |
|---|---|
| `domain/` | только `pydantic` + stdlib |
| `application/` | только `domain/` + stdlib + `pydantic` |
| `nodes/`, `sinks/` | только `domain/` + `application/` |
| `infrastructure/`, `adapters/` | всё выше + внешние клиенты |

Проверка в CI: `grep -r "from infrastructure" domain/ application/ nodes/ sinks/` должен вернуть пустой результат.

Инфраструктурные клиенты (Telethon, asyncpg, Playwright, Qdrant, aiogram, FastMCP) **никогда** не попадают в `domain/`, `application/`, `nodes/`, `sinks/`.

---

## 6 основных протоколов (порты)

Все определены в `application/contracts.py` как `@runtime_checkable Protocol`.

| Протокол | Сигнатура | Назначение |
|---|---|---|
| `Source[T]` | `async fetch() → AsyncIterator[T]` | Асинхронный итератор входящих элементов |
| `Stage[In, Out]` | `async process(item: In) → Out \| None` | Шаг обработки; `None` = дроп элемента |
| `Sink[T]` | `async emit(item: T)` | Финальная запись элемента |
| `Store` | `has_processed / mark_processed / set_run_state / get_run_state` | Хранение состояния дедупликации и запуска |
| `LLMProvider` | `extract[T](text, schema) → T` | Структурированное извлечение через LLM |
| `StoreConnector` | универсальный коннектор → `SQLStoreAdapter` → `PostgreSQLStore` | Трёхуровневая иерархия хранилища |

Дополнительные протоколы для расширяемых слоёв (фазы 11+):

| Протокол | Первая реализация |
|---|---|
| `JobPersistenceBackend` | `SQLiteJobBackend`, `PostgreSQLJobBackend` |
| `SearchBackend` | `PostgreSQLFTSBackend`, `PgVectorBackend` |
| `EmbeddingProvider` | `OpenAIEmbeddingProvider`, `SentenceTransformersProvider` |
| `VectorBackend` | `QdrantVectorBackend`, `PgVectorBackend` |
| `AuthProvider` | `EnvAuthProvider`, `FileAuthProvider`, `VaultAuthProvider` |
| `IngestMode` | `PollingMode`, `EventListenerMode`, `RSSMode`, `WebhookMode`, `WebSocketMode` |
| `BypassStrategy` | `NoopBypass`, `ProxyRotatorBypass`, `StealthBrowserBypass`, `CaptchaSolverBypass` |

---

## Поток данных

### Основной путь (фаза 10+)

```
Source.fetch()
  → SanitizeNode          # первые ворота: карантин при нарушении политики
  → TriageNode            # FilterProfile: пропустить LLM для нерелевантных
  → DedupNode             # rapidFuzz: проверка по ключам в Store
  → ExtractionNode        # RawItem → Job через LLM (instructor)
  → ValidationNode        # нормализация, оценка, маршрутизация review
  → JobGroupNode          # кросс-источниковая агрегация (фаза 25+)
  → main Job Sink
        ↘ review Sink     # пограничные вакансии
        ↘ posting Sink    # публикация в Telegram
        ↘ NotificationSink # рассылка событий (фаза 27+)

side-channels:
  QuarantinedRawItem → quarantine Sink
  RejectedItem       → rejected Sink (любой этап)
  Store              ← → DedupNode / IncrementalCursor
```

### Важные инварианты

- `RawItem` живёт только до `ExtractionNode`. После — только `Job`-объекты.
- `ExtractionNode` — единственная граница смены типа в пайплайне.
- Все остальные узлы — `Stage[Job, Job]` (same-type).
- `None` из любого узла = дроп элемента с записью причины в `RunSummary`.
- `RawItemDropped` = управляемый дроп (дедуп, тема).
- `RawItemRejected` = карантин (политика безопасности).
- Неожиданные исключения изолированы на уровне элемента — запуск продолжается.

---

## Открытый реестр

Все адаптеры регистрируются через декораторы:

```python
@register_source("telegram_channel")
def create_telegram_channel(spec: SourceSpec, auth: AuthProvider) -> Source:
    ...
```

Сторонние пакеты используют entry points:

```toml
[project.entry-points."job_ftch.sources"]
my_custom_source = "my_pkg.sources:create_my_source"
```

Группы entry points: `job_ftch.sources`, `job_ftch.sinks`, `job_ftch.stores`, `job_ftch.parsers`, `job_ftch.notification_targets`.

---

## SourceSpec + AuthProvider

Конфиг источника и учётные данные — всегда разделены:

```yaml
# sources.yaml — безопасно хранить в git
sources:
  - type: telegram_channel
    entity: ai_jobs_channel
    limit: 50
    ingest_mode: polling
    bypass: proxy_rotator
```

```python
# AuthProvider разрешает секреты в рантайме
auth = EnvAuthProvider()  # читает JOB_FTCH_TELEGRAM_API_KEY из env
```

`SourceSpec` — дискриминированный union по полю `type`. Секреты **никогда** не попадают в `SourceSpec` или YAML.

---

## TenantConfig и мультиарендность

```yaml
tenant_id: "company_a"
sources:
  - type: telegram_channel
    entity: ...
notifications:
  trigger: batched
  interval_seconds: 600
  targets:
    - type: webhook
      url: "https://api.company-a.example.com/jobs"
```

`TenantConfig` добавляет `tenant_id` как пространство имён к каждому ключу в Store, индексу в JobBackend и метке в Prometheus. Несколько тенантов изолированы без пересечения данных.

---

## Хранилище данных (трёхуровневая иерархия)

```
StoreConnector  (универсальный протокол)
  └─ SQLStoreAdapter  (СУБД-агностичный SQL-слой)
       ├─ SQLiteStore       (dev / self-hosted, zero infra)
       └─ PostgreSQLStore   (production, asyncpg, no ORM)
```

Аналогично для вакансий:

```
JobPersistenceBackend
  ├─ SQLiteJobBackend   (FTS5, встроенный в SQLite)
  └─ PostgreSQLJobBackend  (FTS + pgvector / Qdrant)
```

Принцип: лёгкий дефолт — SQLite без инфраструктуры. PostgreSQL и Qdrant — опции масштабирования, не обязательные зависимости.

---

## Поиск (протокольный стек)

```
SearchBackend
  ├─ PostgreSQLFTSBackend   (tsvector + GIN индекс)
  └─ PgVectorBackend        (pgvector, FTS + векторный поиск)

EmbeddingProvider
  ├─ OpenAIEmbeddingProvider
  └─ SentenceTransformersProvider  (локально)

VectorBackend
  ├─ QdrantVectorBackend    (первая реализация, оптимизирована для высоких объёмов)
  └─ PgVectorBackend        (удобно если уже используется PostgreSQL)
```

Поиск по умолчанию (SQLite FTS5) требует ноль дополнительной инфраструктуры.

---

## Рассылка событий (Phase 27)

`NotificationSink` реализует `Sink[Job]` и подключается через `FanOutSink`. `Pipeline` не знает о рассылке.

```
NotificationSink
  ├─ trigger: per_job | batched(interval_seconds) | on_count(n) | on_run_complete
  ├─ payload_format: full_job | job_summary | batch | jinja_template
  └─ targets (параллельно, изолированы по сбоям):
       ├─ WebhookTarget  (HTTP POST, HMAC-подпись)
       ├─ NATSTarget     (nats.py)
       ├─ RedisTarget    (Redis Pub/Sub)
       ├─ KafkaTarget    (aiokafka)
       ├─ SlackTarget    (Slack webhook)
       └─ DiscordTarget  (Discord webhook)
```

Конфигурация в YAML в блоке `TenantConfig`. Credentials всегда через `AuthProvider`.

---

## PipelineBuilder

```python
from job_ftch import PipelineBuilder

pipeline = (
    PipelineBuilder()
    .source(SourceSpec(type="telegram_channel", entity="ai_jobs"))
    .source(SourceSpec(type="career_site", url="https://..."))
    .stage(TriageNode(profile=FilterProfile.load("profiles/ai_roles.yaml")))
    .stage(ExtractionNode(llm=OpenAIProvider()))
    .sink(JsonFileSink("artifacts/jobs.json"))
    .sink(NotificationSink(config=NotificationConfig.load("notif.yaml")))
    .store(SQLiteStore(".runtime/store.db"))
    .build()
)

summary = await pipeline.run(max_items=500)
```

---

## Версионирование схемы

`Job` содержит поле `schema_version: int`. При изменении поля оператор указывает политику эволюции:

| Политика | Действие |
|---|---|
| `evolve` | аддитивное изменение, безопасно |
| `freeze` | изменения поля запрещены |
| `discard` | поле удаляется после цикла устаревания |

---

## Жизненный цикл вакансии

```
open → filled       # вакансия закрыта (заполнена)
     → expired      # вакансия устарела
     → delisted     # вакансия исчезла из источника
```

Исчезновение обнаруживается путём сравнения результатов текущего и предыдущего запуска.

---

## Наблюдаемость

| Компонент | Назначение |
|---|---|
| `IncrementalCursor` | Унифицированный водяной знак для всех source-адаптеров |
| `RunHistory` | Персистентная история запусков (время, статистика, ошибки) |
| Lineage graph | Трассировка `raw_item_id → job_id → group_id` |
| `PrometheusExporter` | Метрики: jobs_fetched, jobs_extracted, jobs_failed + по источнику |
| structlog + OTel | JSON-логирование + трейсинг без привязки к вендору |

---

## Связанные документы

- [ADR-001](adr/001-hexagonal-architecture.md) — гексагональная архитектура
- [ADR-007](adr/007-extension-registry-and-plugin-discovery.md) — открытый реестр
- [ADR-010](adr/010-reliability-and-recovery-policies.md) — политики надёжности
- [ADR-011](adr/011-source-spec-auth-provider.md) — SourceSpec + AuthProvider
- [ADR-012](adr/012-store-connector-protocol.md) — иерархия StoreConnector
- [ADR-013](adr/013-filter-profile-configurable-relevance.md) — FilterProfile
- [ADR-014](adr/014-search-embedding-vector-protocol-stack.md) — поисковый стек
- [ADR-015](adr/015-ingestion-mode-bypass-strategy.md) — IngestMode + BypassStrategy
- [ADR-016](adr/016-job-group-cross-source-aggregation.md) — JobGroup агрегация
- [ADR-017](adr/017-notification-sink-event-broadcasting.md) — рассылка событий
- [Роадмап](roadmap.md)
- [Технологический стек](tech_stack.md)
