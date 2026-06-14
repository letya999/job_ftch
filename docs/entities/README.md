# Сущности системы

Этот раздел содержит описание ключевых структур данных и контрактов, используемых в системе `job_ftch`.

## Раздел 1: Карта сущностей

Ниже представлены основные сущности, сгруппированные по архитектурным слоям.

### Domain Layer (Слой предметной области)
Чистые модели данных, не зависящие от внешних фреймворков.

*   [RawItem](raw_item.md) — сырой элемент с источника до обработки.
*   [JobDraft](job_draft.md) — структурированный черновик после LLM-экстракции.
*   [JobRecord](job_record.md) — канонический контракт вакансии (финальный).
*   [JobGroup](job_group.md) — кросс-сурсовая группа одинаковых вакансий.
*   [SourceSpec](source_spec.md) — типизированный дескриптор источника (discriminated union).
*   [CandidateProfile](candidate_profile.md) — профиль кандидата с поисковыми критериями.
*   SearchProfile — параметры одного профиля поиска внутри CandidateProfile.
*   SkillTag — нормализованный навык.
*   FilterProfile — параметры фильтрации (deprecated, заменяется на SearchProfile).
*   [ProfileCatalog](profile_catalog.md) — набор SearchProfile для мультипрофильного матчинга.
*   ManagedCandidateProfile — профиль с метаданными управления (user_id, updated_at).
*   [RunSummary](run_summary.md) — итоги одного запуска пайплайна.
*   RawItemDropped — управляемый дроп (дедупликация, несоответствие теме).
*   RawItemRejected — карантин (нарушение политик безопасности).
*   [TenantConfig](tenant_config.md) — конфигурация одного тенанта (источники, уведомления).
*   ProviderCredentials / AuthSpec — учётные данные для доступа к источникам.

### Application Layer (Слой приложения)
Протоколы (интерфейсы) и логика оркестрации.

*   [Source[T]](protocols.md) — Protocol: асинхронный итератор элементов.
*   [Stage[In, Out]](protocols.md) — Protocol: узел обработки данных.
*   [Sink[T]](protocols.md) — Protocol: вывод результатов (Telegram, JSON и др.).
*   [Store](store.md) — Protocol: хранилище состояний запусков и дедупликации.
*   StoreConnector — расширение Store с методами управления соединением.
*   [LLMProvider](protocols.md) — Protocol: интерфейс для экстракции данных через LLM.
*   [AuthProvider](protocols.md) — Protocol: разрешение учётных данных.
*   SearchBackend — Protocol: поиск по вакансиям.
*   EmbeddingProvider — Protocol: получение векторных представлений текста.
*   VectorBackend — Protocol: хранение и поиск по векторам.
*   JobPersistenceBackend — Protocol: CRUD операции для JobRecord.
*   [BypassStrategy](bypass_strategy.md) — Protocol: стратегии обхода защиты от ботов.
*   IngestMode — Protocol: режимы получения данных (polling, webhook).
*   [PipelineBuilder](pipeline_builder.md) — fluent builder для сборки пайплайна.
*   Pipeline — исполняемый пайплайн обработки.
*   TenantRunner — мультиарендный раннер для управления тенантами.
*   [PluginRegistry](plugins.md) — реестр типизированных плагинов.
*   PluginDescriptor — метаданные плагина (тип, имя, фабрика).
*   PluginKind — перечисление видов плагинов.
*   PluginState — состояния жизненного цикла плагина.

### Infrastructure Layer (Слой инфраструктуры)
Конкретные реализации протоколов и работа с внешними системами.

*   SQLiteStore / PostgreSQLStore — реализации Store.
*   SQLiteJobBackend / PostgreSQLJobBackend — реализации JobPersistenceBackend.
*   OpenAIProvider — реализация LLMProvider.
*   OpenAIEmbeddingProvider / SentenceTransformersProvider / OllamaProvider.
*   QdrantVectorBackend / PgVectorBackend.
*   EnvAuthProvider / FileAuthProvider / VaultAuthProvider.
*   CareerSiteConfig — декларативная конфигурация для сайтов вакансий.
*   MonitorEntry / ScraperEntry — записи во внутренних реестрах мониторов.

### Adapters Layer (Слой адаптеров)
Интерфейсы для взаимодействия с внешним миром.

*   [RuntimeAdapter](runtime_adapters.md) — концепция обёртки пайплайна.
*   FastAPI adapter — REST API интерфейс.
*   MCP adapter — реализация Model Context Protocol сервера.
*   Dagster adapter — интеграция с оркестратором Dagster.
*   FastStream adapter — интеграция с брокерами сообщений.
*   Telegram Bot — интерфейс взаимодействия через Telegram.

## Раздел 2: Жизненный цикл вакансии

Процесс трансформации данных от сырого текста до финальной записи:

RawItem → (SanitizeNode) → QuarantinedRawItem [если нарушение политики]
        → (LanguageContextNode)
        → (PostTypeClassificationNode)
        → (HardFilterNode) → RawItemDropped [если тема/спам]
        → (DedupNode) → RawItemDropped [если дубль]
        → (SemanticPrefilterNode) → RawItemDropped [если нерелевантно]
        → (ExtractionNode) → JobDraft
        → (ExtractionValidationNode)
        → (TitleCompanyNormalizationNode)
        → (LocationWorkModeNormalizationNode)
        → (CompensationParsingNode)
        → (MultiProfileMatchNode)
        → (RiskScoringNode)
        → (QualityScoringNode)
        → (JobValidationNode) → RejectedItem [если критическая ошибка]
        → (JobAggregationNode) → JobRecord + JobGroup
        → Sinks

## Раздел 3: Что такое Plugin vs RuntimeAdapter

Плагины (Plugins) являются расширениями самого ядра системы. Они реализуют
базовые протоколы (Source, Stage, Sink) и позволяют добавлять новые способы
сбора данных или алгоритмы обработки, которые становятся доступны внутри
любого пайплайна. Плагины "живут" внутри PipelineBuilder.

Адаптеры времени выполнения (RuntimeAdapters) — это внешние оболочки вокруг
собранного пайплайна. Они определяют, как именно запускается система: как
часть веб-сервера FastAPI, как воркер в Dagster или как интерактивный
Telegram-бот. Адаптеры знают о существовании PipelineBuilder, но сами плагины
ничего не знают об адаптерах.

## Раздел 4: Навигация по документам

*   [RawItem — Сырой элемент](raw_item.md)
*   [JobDraft — Черновик вакансии](job_draft.md)
*   [JobRecord — Каноническая запись](job_record.md)
*   [JobGroup — Группа вакансий](job_group.md)
*   [SourceSpec — Спецификация источника](source_spec.md)
*   [CandidateProfile — Профиль кандидата](candidate_profile.md)
*   [ProfileCatalog — Каталог профилей](profile_catalog.md)
*   [RunSummary — Итоги запуска](run_summary.md)
*   [Protocols — Основные протоколы](protocols.md)
*   [BypassStrategy — Обход защит](bypass_strategy.md)
*   [Store — Хранилище состояний](store.md)
*   [Plugins — Система плагинов](plugins.md)
*   [RuntimeAdapters — Адаптеры среды](runtime_adapters.md)
*   [TenantConfig — Конфигурация тенантов](tenant_config.md)
*   [PipelineBuilder — Сборка пайплайна](pipeline_builder.md)
