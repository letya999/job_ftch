# job_ftch

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-ранняя%20разработка-orange.svg)

**job_ftch** — library-first асинхронный движок для сбора вакансий. Собирает данные из разнородных источников (Telegram-каналы/группы, карьерные сайты, официальные API, ATS-вебхуки), нормализует в типизированные записи `Job` и отправляет в подключаемые sinks — без привязки к конкретному оркестратору. Любая обёртка (CLI, FastStream, FastAPI, Dagster, Airflow, MCP-сервер, Telegram-бот) — это адаптер поверх ядра, а не часть ядра.

---

## Эволюция архитектуры

Система растёт через 5 качественных переходов. Каждый слайд показывает горизонтальный состав компонентов и их ключевые обязанности на данном этапе.

### Переход 1 — Фаза 10: Линейный MVP-пайплайн (сдан)

Два источника, один этап LLM-извлечения, дедупликация в памяти, вывод в JSON.

```mermaid
graph LR
    subgraph SRC["Источники"]
        TG["Telegram\nКанал · Группа · Комментарии"]
        CS["CareerSite\nдекларативный HTML"]
    end
    subgraph PIPE["Пайплайн  (app.py)"]
        SAN["SanitizeNode\nворота карантина"]
        TRI["TriageNode\nэвристические ключевые слова"]
        DED["DedupNode\nrapidFuzz"]
        EXT["ExtractionNode\nRawItem → Job через LLM"]
        VAL["ValidationNode\nнормализация · оценка"]
    end
    subgraph SINK["Sinks"]
        JS["JsonFileSink\nосновной"]
        RV["JsonFileSink\nревью"]
        RJ["JsonFileSink\nотклонённые"]
        QU["JsonFileSink\nкарантин"]
    end
    subgraph STATE["Состояние"]
        MS["MemoryStore\nключи дедупликации · маркеры запуска"]
    end
    TG & CS --> SAN --> TRI --> DED --> EXT --> VAL --> JS
    VAL -.-> RV
    SAN -.-> QU
    EXT -.-> RJ
    DED <--> MS
```

---

### Переход 2 — Фаза 13: Мультиисточники + открытый реестр + персистентное хранилище

Декларативный `sources.yaml`, открытый реестр `@register_source`, `SQLiteStore` переживает перезапуск, `FilterProfile` заменяет захардкоженные ключевые слова.

```mermaid
graph LR
    subgraph CFG["Слой конфигурации"]
        YAML["sources.yaml\nсписок SourceSpec"]
        FP["FilterProfile\nнастраиваемая релевантность"]
    end
    subgraph REG["Открытый реестр"]
        SR["@register_source · @register_sink\nзагрузчик entry_points"]
    end
    subgraph SRC["Источники (fan-in)"]
        TG["Telegram-источники × N"]
        CS["CareerSite-источники × N"]
        DBG["DebugSource · фикстуры"]
    end
    COMP["CompositeSource\nобъединяет N async-итераторов"]
    subgraph PIPE["Пайплайн"]
        SAN["SanitizeNode"]
        TRI["TriageNode  на основе FilterProfile"]
        DED["DedupNode"]
        EXT["ExtractionNode LLM"]
        VAL["ValidationNode"]
    end
    subgraph STORE["Персистентное состояние"]
        SQST["SQLiteStore\nдедупликация · состояние запуска · курсоры"]
    end
    subgraph SINK["Sinks"]
        JS["JsonFileSink"]
        SQLS["SQLiteJobSink\nзапрашиваемый"]
    end
    YAML --> REG --> SRC
    FP --> TRI
    SRC --> COMP --> SAN --> TRI --> DED --> EXT --> VAL --> JS & SQLS
    DED <--> SQST
```

---

### Переход 3 — Фаза 17: Поиск + планировщик + API-адаптеры + обход защиты

Полнотекстовый и векторный поиск, периодическое планирование, официальные API вакансий, подключаемые стратегии обхода защищённых сайтов.

```mermaid
graph LR
    subgraph SCH["Планировщик"]
        APSch["APScheduler\ncron / интервал · режим демона"]
    end
    subgraph SRC["Источники"]
        TG["Telegram-источники"]
        CS["CareerSite HTML"]
        API["Официальные API\nHH.ru · LinkedIn · Greenhouse · Lever"]
        WS["WebSocketSource\nпотоки реального времени"]
        BYP["BypassStrategy\nПрокси · Капча · StealthBrowser"]
    end
    subgraph PIPE["Ядро пайплайна"]
        COMP["CompositeSource"]
        CORE["Sanitize → Triage → Dedup\n→ Extract → Validate"]
    end
    subgraph STORAGE["Хранилище"]
        JB_S["SQLiteJobBackend\nFTS5  JobPersistenceBackend"]
        SB_S["Протокол SearchBackend\nPostgreSQLFTSBackend"]
        VB["Протокол VectorBackend\nQdrantVectorBackend\nсемантический поиск"]
        EP["EmbeddingProvider\nOpenAI · SentenceTransformers"]
        HIST["RunHistory\nстатистика + тайминги запусков"]
    end
    subgraph OUT["Вывод"]
        JS["JsonFileSink"]
        POST["TelegramPublishSink"]
    end
    APSch --> COMP
    SRC --> COMP
    BYP -.внедрение.-> SRC
    COMP --> CORE --> JS & POST
    CORE --> JB_S & SB_S
    SB_S --> VB --> EP
    CORE --> HIST
```

---

### Переход 4 — Фаза 22: Пакетированная библиотека + мультитенантность + MCP-сервер

Весь код под пакетом `job_ftch/`, fluent API `PipelineBuilder`, изоляция `TenantConfig`, FastMCP-сервер предоставляет инструменты и ресурсы Claude Code, Cursor и другим MCP-клиентам.

```mermaid
graph LR
    subgraph LIB["job_ftch  (pip install job_ftch)"]
        PB["PipelineBuilder\n.source().stage().sink().build()"]
        TC["TenantConfig\nпространство имён tenant_id · изоляция"]
        AUTH["AuthProvider\nEnv · File · Vault"]
        subgraph CORE["Ядро пайплайна"]
            COMP["CompositeSource"]
            PIPE["Sanitize → Triage → Dedup\n→ Extract → Validate"]
        end
        subgraph BACKENDS["Бэкенды"]
            PG["PostgreSQLJobBackend\nFTS + pgvector"]
            QD["QdrantVectorBackend\nсемантический поиск"]
        end
    end
    subgraph ADAPTERS["Адаптеры рантайма"]
        MCP["FastMCP-сервер\nstdio + SSE / HTTP\nинструменты: search_jobs · run_pipeline\nресурсы: job://"]
        FST["FastStream-воркер\nпотребитель очереди"]
        SCHED["Демон-планировщик"]
    end
    subgraph CLIENTS["MCP-клиенты"]
        CC["Claude Code"]
        CUR["Cursor / IDE"]
        DT["Claude Desktop"]
        OC["OpenCode · Antigravity"]
    end
    PB --> CORE
    TC & AUTH --> CORE
    CORE --> BACKENDS
    MCP & FST & SCHED --> CORE
    CC & CUR & DT & OC --> MCP
```

---

### Переход 5 — Фаза 27: Полная платформа (финальное состояние роадмапа)

Богатая доменная модель (жизненный цикл, каноникализация, версионирование схемы), кросс-источниковая агрегация, наблюдаемость и настраиваемая рассылка событий.

```mermaid
graph LR
    subgraph SOURCES["Источники (открытый реестр)"]
        TG["Telegram\nканал · группа · комментарии"]
        CS["CareerSite HTML"]
        APIs["Официальные API\nHH · LinkedIn · Greenhouse"]
        WH_IN["WebhookSource\nвходящий ATS push"]
        RT["WebSocketSource\nреальное время"]
    end
    subgraph CORE["Ядро job_ftch"]
        PIPE["Пайплайн\nSanitize → Triage → Dedup\n→ Extract → Validate → Group"]
        SCHED["Планировщик"]
        TC["TenantConfig"]
        AUTH["AuthProvider"]
    end
    subgraph DOMAIN["Богатый домен"]
        JG["JobGroup\nкросс-источниковая агрегация\nmatching по идентичности"]
        LC["Жизненный цикл\nopen → filled → expired → delisted"]
        CANON["Каноникализация компании\nтаблица псевдонимов + нечёткое совпадение"]
        SV["schema_version\nполитика эволюции"]
    end
    subgraph STORE["Хранилище"]
        SQST["SQLiteStore  (dev)"]
        PGJB["PostgreSQL\nбэкенд вакансий + FTS"]
        QD["Qdrant\nвекторный бэкенд"]
    end
    subgraph OBS["Наблюдаемость"]
        IC["IncrementalCursor\nунифицированный водяной знак"]
        LIN["Линейность\nraw_item → job → group"]
        PROM["Prometheus-экспортёр"]
        HIST["RunHistory"]
    end
    subgraph BROADCAST["Рассылка событий"]
        NS["NotificationSink\nbatched · per_job · on_run_complete"]
        WHT["WebhookTarget\nс HMAC-подписью"]
        NATS["NATSTarget"]
        SLACK["SlackTarget · DiscordTarget"]
    end
    subgraph ADAPTERS["Адаптеры"]
        MCP["FastMCP-сервер\n15+ инструментов · ресурсы job://"]
        BOT["Telegram-бот\naiogram · /search · /subscribe · /digest"]
        FAPI["FastAPI-мост\nрежим вебхука"]
        FST["FastStream-воркер"]
    end
    SOURCES --> PIPE
    SCHED & TC & AUTH --> PIPE
    PIPE --> DOMAIN --> STORE
    PIPE --> OBS
    PIPE --> NS --> WHT & NATS & SLACK
    MCP & BOT & FAPI & FST --> PIPE
```

---

## Архитектура — C4 (финальное состояние роадмапа)

### Уровень 1: Системный контекст

```mermaid
C4Context
    title Системный контекст — job_ftch

    Person(operator, "Оператор", "Настраивает источники, фильтры, sinks через YAML / CLI")
    Person(enduser, "Конечный пользователь", "Ищет вакансии, получает дайджесты через бот или MCP-клиент")

    System(jf, "job_ftch", "Асинхронный движок сбора вакансий. Получает, нормализует, дедублирует, хранит и рассылает вакансии из разнородных источников.")

    System_Ext(telegram, "Telegram", "MTProto: каналы, группы, ветки комментариев")
    System_Ext(careersites, "Карьерные сайты", "Greenhouse, Lever, Workday, кастомные HTML-доски")
    System_Ext(jobapis, "Официальные API вакансий", "HH.ru, LinkedIn, Greenhouse API, Lever API")
    System_Ext(ats, "ATS / push", "Входящие webhook-события от систем найма")
    System_Ext(llm, "LLM-провайдер", "OpenAI, локальные модели через instructor")

    System_Ext(postgres, "PostgreSQL", "Хранение вакансий, FTS-индекс, история запусков")
    System_Ext(qdrant, "Qdrant", "Векторное хранилище для семантического поиска")
    System_Ext(eventbus, "NATS / Redis / Kafka", "Исходящая шина событий")
    System_Ext(notif, "Slack / Discord / Webhook", "Эндпоинты уведомлений")
    System_Ext(mcpclients, "MCP-клиенты", "Claude Code, Cursor, Claude Desktop, OpenCode")

    Rel(operator, jf, "Настраивает и запускает")
    Rel(enduser, jf, "Ищет, подписывается, получает дайджест", "Telegram-бот / MCP")

    Rel(jf, telegram, "Получает сообщения", "Telethon MTProto")
    Rel(jf, careersites, "Краулит листинги", "httpx + selectolax")
    Rel(jf, jobapis, "Вызывает REST API", "httpx")
    Rel(ats, jf, "Отправляет события вакансий", "HTTP входящий webhook")
    Rel(jf, llm, "Извлекает структурированные поля", "instructor + openai SDK")

    Rel(jf, postgres, "Хранит вакансии, FTS, историю", "asyncpg")
    Rel(jf, qdrant, "Индексирует и запрашивает эмбеддинги", "qdrant-client")
    Rel(jf, eventbus, "Публикует события вакансий", "nats.py / aiokafka")
    Rel(jf, notif, "Отправляет пакетные уведомления", "HTTP webhook")
    Rel(mcpclients, jf, "Вызывает инструменты, читает ресурсы", "MCP stdio / SSE")
```

---

### Уровень 2: Контейнеры

```mermaid
C4Container
    title Диаграмма контейнеров — job_ftch

    Person(operator, "Оператор")
    Person(enduser, "Конечный пользователь")

    Container_Boundary(jf, "Система job_ftch") {
        Container(cli, "CLI-раннер", "Python / app.py", "Собирает пайплайн из Settings / TenantConfig и запускает однократно или как демон.")
        Container(mcp, "FastMCP-сервер", "Python / FastMCP", "MCP-протокол. 15+ инструментов и ресурсы job://. Транспорты: stdio + SSE.")
        Container(bot, "Telegram-бот", "Python / aiogram", "/search, /subscribe, /digest. Polling; опциональный FastAPI webhook-мост.")
        Container(fst, "FastStream-воркер", "Python / FastStream", "Оборачивает пайплайн как потребитель/производитель очереди сообщений.")

        Container(pipeline, "Ядро пайплайна", "Python / asyncio", "Поэлементная оркестрация: получение из Source → цепочка nodes → отправка в Sink. RunSummary, обработка исключений.")
        Container(sources, "Source-адаптеры", "Python", "Telegram (MTProto), CareerSite (HTML), официальные API, WebhookSource, WebSocketSource, DebugSource.")
        Container(nodes, "Узлы обработки", "Python", "SanitizeNode, TriageNode, DedupNode, ExtractionNode (LLM), ValidationNode, JobGroupNode.")
        Container(sinks, "Sink-адаптеры", "Python", "JsonFileSink, SQLiteJobSink, TelegramPublishSink, NotificationSink, FanOutSink.")

        Container(store, "Store-бэкенды", "asyncpg / aiosqlite", "SQLiteStore, PostgreSQLStore. Ключи дедупликации, состояние запуска, IncrementalCursor.")
        Container(jobbackend, "Job-бэкенды", "asyncpg / aiosqlite", "SQLiteJobBackend (FTS5), PostgreSQLJobBackend. Протокол JobPersistenceBackend.")
        Container(vectorbackend, "Векторный бэкенд", "qdrant-client", "QdrantVectorBackend. EmbeddingProvider: OpenAI / SentenceTransformers.")
        Container(registry, "Реестр расширений", "Python / entry_points", "Открытый декоратор @register_* + загрузчик entry_points. Новые адаптеры без правок ядра.")
        Container(obs, "Наблюдаемость", "prometheus-client / structlog", "PrometheusExporter, RunHistory, граф линейности, IncrementalCursor.")
    }

    System_Ext(tg_ext, "Telegram")
    System_Ext(llm_ext, "LLM-провайдер")
    System_Ext(pg_ext, "PostgreSQL")
    System_Ext(qd_ext, "Qdrant")
    System_Ext(notif_ext, "Эндпоинты уведомлений")
    System_Ext(mcp_ext, "MCP-клиенты")

    Rel(operator, cli, "Запускает пайплайн", "CLI / env / YAML")
    Rel(enduser, bot, "Отправляет команды", "Telegram")
    Rel(mcp_ext, mcp, "Вызывает инструменты", "MCP stdio/SSE")

    Rel(cli, pipeline, "Собирает и запускает")
    Rel(mcp, pipeline, "Запускает, запрашивает")
    Rel(bot, pipeline, "Запускает, запрашивает")
    Rel(fst, pipeline, "Обрабатывает сообщения очереди")

    Rel(pipeline, sources, "fetch()")
    Rel(pipeline, nodes, "process()")
    Rel(pipeline, sinks, "emit()")
    Rel(pipeline, store, "дедупликация + состояние запуска")
    Rel(pipeline, jobbackend, "сохранение + поиск")
    Rel(pipeline, vectorbackend, "индексация + поиск эмбеддингов")
    Rel(pipeline, obs, "метрики, линейность, история")

    Rel(sources, tg_ext, "Telethon MTProto")
    Rel(nodes, llm_ext, "instructor / openai SDK")
    Rel(store, pg_ext, "asyncpg")
    Rel(jobbackend, pg_ext, "asyncpg")
    Rel(vectorbackend, qd_ext, "qdrant-client")
    Rel(sinks, notif_ext, "HTTP / NATS / Kafka")

    Rel(registry, sources, "Обнаруживает и инстанцирует")
    Rel(registry, sinks, "Обнаруживает и инстанцирует")
```

---

### Уровень 3: Компоненты ядра пайплайна

```mermaid
C4Component
    title Диаграмма компонентов — ядро пайплайна

    Container_Boundary(app, "application/") {
        Component(orch, "Pipeline", "pipeline.py", "Асинхронный поэлементный оркестратор. Управляет цепочкой source → nodes → sink. Записывает RunSummary, обрабатывает RawItemDropped / RawItemRejected.")
        Component(contracts, "Contracts", "contracts.py", "@runtime_checkable Protocol: Source, Stage[In,Out], SanitizingNode, ProcessingNode, Sink, Store, LLMProvider, StoreConnector, JobPersistenceBackend, SearchBackend, EmbeddingProvider, VectorBackend, AuthProvider, IngestMode, BypassStrategy.")
        Component(reg, "Registry", "registry.py", "Открытый декоратор @register_* + загрузчик entry_points для источников, sinks, хранилищ, LLM, парсеров, целей уведомлений.")
        Component(builder, "PipelineBuilder", "builder.py", "Fluent API: .source(spec).stage(node).sink(s).store(s).build() → Pipeline.")
        Component(drops, "Drops / Rejections", "drops.py / rejections.py", "Типизированная иерархия исключений для управления потоком на уровне элементов внутри Pipeline.run().")
    }

    Container_Boundary(nodes_b, "nodes/") {
        Component(san, "SanitizeNode", "sanitize.py", "Первые ворота. Макс. длина, исправление кодировки, карантин при нарушении политики.")
        Component(tri, "TriageNode", "triage.py", "Предфильтр на основе FilterProfile. Пропускает вызов LLM для нерелевантных элементов.")
        Component(ded, "DedupNode", "dedup.py", "Проверка почти-дублей через rapidFuzz по обработанным ключам в Store.")
        Component(ext, "ExtractionNode", "extraction.py", "RawItem → Job через LLM (instructor). Эвристический fallback при частичном извлечении.")
        Component(val, "ValidationNode", "validation.py", "Нормализация title/company/location, парсинг зарплаты, оценка, маршрутизация пограничных случаев в review sink.")
        Component(grp, "JobGroupNode", "job_group.py", "Кросс-источниковая агрегация. Fingerprint + сходство эмбеддингов → слияние в JobGroup, выбор канонической вакансии.")
    }

    Container_Boundary(dom, "domain/") {
        Component(job, "Job / RawItem", "job.py / raw_item.py", "Pydantic-модели. Job содержит schema_version, статус жизненного цикла, group_id, линейность raw_item_id.")
        Component(jgm, "JobGroup", "job_group.py", "Агрегированное представление одной вакансии, наблюдаемой из N источников.")
        Component(cur, "IncrementalCursor", "cursor.py", "Унифицированный водяной знак: last_seen_id, last_seen_at, page_token. Используется всеми source-адаптерами.")
        Component(fp, "FilterProfile", "filter_profile.py", "Настраиваемая релевантность: positive_keywords, negative_keywords, required_patterns, min_score.")
        Component(nc, "NotificationConfig", "notification.py", "Что/когда/куда рассылать: trigger, targets, payload_format, фильтр min_score.")
    }

    Rel(orch, san, "process()")
    Rel(orch, tri, "process()")
    Rel(orch, ded, "process()")
    Rel(orch, ext, "process()")
    Rel(orch, val, "process()")
    Rel(orch, grp, "process()")
    Rel(orch, contracts, "Типы протоколов")
    Rel(builder, orch, "Создаёт")
    Rel(reg, builder, "Предоставляет фабрики")
    Rel(ext, job, "Производит Job")
    Rel(grp, jgm, "Производит JobGroup")
    Rel(ded, cur, "Обновляет водяной знак")
    Rel(tri, fp, "Читает FilterProfile")
```

---

## Быстрый старт

```bash
git clone https://github.com/[owner]/job_ftch
cd job_ftch
uv sync
cp .env.example .env
```

Запуск с тестовыми данными (учётные данные не требуются):

```bash
uv run python app.py \
  --source-path fixtures/e2e/multisource_positive.jsonl \
  --output-path artifacts/debug/jobs.json \
  --max-items 20
```

Запуск на Telegram-канале:

```bash
uv run python app.py \
  --source-backend telegram_channel \
  --telegram-entity ai_jobs \
  --max-items 100
```

Запуск на карьерном сайте:

```bash
uv run python app.py \
  --source-backend career_site \
  --career-site-url https://job-boards.greenhouse.io/clickhouse
```

Оффлайн-оценка качества извлечения:

```bash
uv run python scripts/evaluate_extraction.py \
  --fixture fixtures/extraction/gold_samples.jsonl \
  --llm-backend heuristic
```

Результаты:
- `artifacts/debug/jobs.json` — основные вакансии
- `artifacts/debug/review.jsonl` — пограничные элементы для ревью оператора
- `artifacts/debug/rejected.jsonl` — отклонённые элементы с причинами
- `artifacts/debug/quarantine.jsonl` — заблокированные на этапе санитизации

---

## Документация

| Тема | Файл |
|---|---|
| Архитектура (подробные C4 + принципы) | [docs/architecture.md](docs/architecture.md) |
| Технологический стек | [docs/tech_stack.md](docs/tech_stack.md) |
| Правила разработки | [docs/rules.md](docs/rules.md) |
| Роадмап | [docs/roadmap.md](docs/roadmap.md) |
| Справочник конфигурации | [docs/configuration.md](docs/configuration.md) |
| Настройка источников | [docs/source_setup.md](docs/source_setup.md) |
| Примеры | [docs/examples.md](docs/examples.md) |
| Устранение неполадок | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Чеклист релиза | [docs/release_checklist.md](docs/release_checklist.md) |
| ADR (архитектурные решения) | [docs/adr/](docs/adr/) |

## Участие в разработке

См. [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия

MIT
