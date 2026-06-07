# job_ftch

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-early%20development-orange.svg)

**job_ftch** is a library-first async ingestion engine for job postings. It fetches from heterogeneous sources (Telegram channels/groups, career sites, official APIs, ATS webhooks), normalises into typed `Job` records, and emits to pluggable sinks — decoupled from any runtime orchestrator. Any wrapper (CLI, FastStream, FastAPI, Dagster, Airflow, MCP server, Telegram bot) is an adapter on top, not part of the core.

---

## Evolutionary architecture

The system grows through 5 qualitative milestones. Each slide shows the horizontal component layout and key responsibilities at that point.

### Milestone 1 — Phase 10: Linear MVP pipeline (shipped)

Two sources, one LLM extraction stage, in-memory dedup, JSON output.

```mermaid
graph LR
    subgraph SRC["Sources"]
        TG["Telegram\nChannel · Group · Comment"]
        CS["CareerSite\ndeclarative HTML"]
    end
    subgraph PIPE["Pipeline  (app.py)"]
        SAN["SanitizeNode\nquarantine gate"]
        TRI["TriageNode\nheuristic keywords"]
        DED["DedupNode\nrapidFuzz"]
        EXT["ExtractionNode\nRawItem → Job via LLM"]
        VAL["ValidationNode\nnormalise · score"]
    end
    subgraph SINK["Sinks"]
        JS["JsonFileSink\nmain"]
        RV["JsonFileSink\nreview"]
        RJ["JsonFileSink\nrejected"]
        QU["JsonFileSink\nquarantine"]
    end
    subgraph STATE["State"]
        MS["MemoryStore\ndedup keys · run markers"]
    end
    TG & CS --> SAN --> TRI --> DED --> EXT --> VAL --> JS
    VAL -.-> RV
    SAN -.-> QU
    EXT -.-> RJ
    DED <--> MS
```

---

### Milestone 2 — Phase 13: Multi-source + open registry + persistent store

Declarative `sources.yaml`, `@register_source` open registry, `SQLiteStore` survives restarts, `FilterProfile` replaces hardcoded keywords.

```mermaid
graph LR
    subgraph CFG["Config layer"]
        YAML["sources.yaml\nSourceSpec list"]
        FP["FilterProfile\nconfigurable relevance"]
    end
    subgraph REG["Open registry"]
        SR["@register_source · @register_sink\nentry_points loader"]
    end
    subgraph SRC["Sources (fan-in)"]
        TG["Telegram sources × N"]
        CS["CareerSite sources × N"]
        DBG["DebugSource · fixtures"]
    end
    COMP["CompositeSource\nmerges N async iterators"]
    subgraph PIPE["Pipeline"]
        SAN["SanitizeNode"]
        TRI["TriageNode  FilterProfile-driven"]
        DED["DedupNode"]
        EXT["ExtractionNode LLM"]
        VAL["ValidationNode"]
    end
    subgraph STORE["Persistent state"]
        SQST["SQLiteStore\ndedup · run state · cursors"]
    end
    subgraph SINK["Sinks"]
        JS["JsonFileSink"]
        SQLS["SQLiteJobSink\nqueryable"]
    end
    YAML --> REG --> SRC
    FP --> TRI
    SRC --> COMP --> SAN --> TRI --> DED --> EXT --> VAL --> JS & SQLS
    DED <--> SQST
```

---

### Milestone 3 — Phase 17: Search + scheduler + API adapters + bypass

Full-text and vector search, periodic scheduling, official job API sources, pluggable bypass strategies for protected sites.

```mermaid
graph LR
    subgraph SCH["Scheduler"]
        APSch["APScheduler\ncron / interval · daemon mode"]
    end
    subgraph SRC["Sources"]
        TG["Telegram sources"]
        CS["CareerSite HTML"]
        API["Official APIs\nHH.ru · LinkedIn · Greenhouse · Lever"]
        WS["WebSocketSource\nrealtime streams"]
        BYP["BypassStrategy\nProxy · Captcha · StealthBrowser"]
    end
    subgraph PIPE["Pipeline core"]
        COMP["CompositeSource"]
        CORE["Sanitize → Triage → Dedup\n→ Extract → Validate"]
    end
    subgraph STORAGE["Storage"]
        JB_S["SQLiteJobBackend\nFTS5  JobPersistenceBackend"]
        SB_S["SearchBackend protocol\nPostgreSQLFTSBackend"]
        VB["VectorBackend protocol\nQdrantVectorBackend\nsemantic search"]
        EP["EmbeddingProvider\nOpenAI · SentenceTransformers"]
        HIST["RunHistory\nrun stats + timing"]
    end
    subgraph OUT["Output"]
        JS["JsonFileSink"]
        POST["TelegramPublishSink"]
    end
    APSch --> COMP
    SRC --> COMP
    BYP -.injected.-> SRC
    COMP --> CORE --> JS & POST
    CORE --> JB_S & SB_S
    SB_S --> VB --> EP
    CORE --> HIST
```

---

### Milestone 4 — Phase 22: Packaged library + multi-tenant + MCP server

All code under `job_ftch/` package, `PipelineBuilder` fluent API, `TenantConfig` isolation, FastMCP server exposes tools and resources to Claude Code, Cursor, and other MCP clients.

```mermaid
graph LR
    subgraph LIB["job_ftch  (pip install job_ftch)"]
        PB["PipelineBuilder\n.source().stage().sink().build()"]
        TC["TenantConfig\ntenant_id namespace · per-tenant isolation"]
        AUTH["AuthProvider\nEnv · File · Vault"]
        subgraph CORE["Pipeline core"]
            COMP["CompositeSource"]
            PIPE["Sanitize → Triage → Dedup\n→ Extract → Validate"]
        end
        subgraph BACKENDS["Backends"]
            PG["PostgreSQLJobBackend\nFTS + pgvector"]
            QD["QdrantVectorBackend\nsemantic search"]
        end
    end
    subgraph ADAPTERS["Runtime adapters"]
        MCP["FastMCP server\nstdio + SSE / HTTP\ntools: search_jobs · run_pipeline\nresources: job://"]
        FST["FastStream worker\nqueue consumer"]
        SCHED["Scheduler daemon"]
    end
    subgraph CLIENTS["MCP clients"]
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

### Milestone 5 — Phase 27: Full platform (final roadmap state)

Rich domain (lifecycle, canonicalization, schema versioning), cross-source aggregation, observability, and configurable event broadcasting.

```mermaid
graph LR
    subgraph SOURCES["Sources (open registry)"]
        TG["Telegram\nchannel · group · comment"]
        CS["CareerSite HTML"]
        APIs["Official APIs\nHH · LinkedIn · Greenhouse"]
        WH_IN["WebhookSource\nATS push inbound"]
        RT["WebSocketSource\nrealtime"]
    end
    subgraph CORE["job_ftch core"]
        PIPE["Pipeline\nSanitize → Triage → Dedup\n→ Extract → Validate → Group"]
        SCHED["Scheduler"]
        TC["TenantConfig"]
        AUTH["AuthProvider"]
    end
    subgraph DOMAIN["Rich domain"]
        JG["JobGroup\ncross-source aggregation\nidentity matching"]
        LC["Lifecycle\nopen → filled → expired → delisted"]
        CANON["Company canonicalization\nalias table + fuzzy match"]
        SV["schema_version\nevolution policy"]
    end
    subgraph STORE["Storage"]
        SQST["SQLiteStore  (dev)"]
        PGJB["PostgreSQL\njob backend + FTS"]
        QD["Qdrant\nvector backend"]
    end
    subgraph OBS["Observability"]
        IC["IncrementalCursor\nunified watermark"]
        LIN["Lineage\nraw_item → job → group"]
        PROM["Prometheus exporter"]
        HIST["RunHistory"]
    end
    subgraph BROADCAST["Event broadcasting"]
        NS["NotificationSink\nbatched · per_job · on_run_complete"]
        WHT["WebhookTarget\nHMAC signed"]
        NATS["NATSTarget"]
        SLACK["SlackTarget · DiscordTarget"]
    end
    subgraph ADAPTERS["Adapters"]
        MCP["FastMCP server\n15+ tools · job:// resources"]
        BOT["Telegram bot\naiogram · /search · /subscribe · /digest"]
        FAPI["FastAPI bridge\nwebhook mode"]
        FST["FastStream worker"]
    end
    SOURCES --> PIPE
    SCHED & TC & AUTH --> PIPE
    PIPE --> DOMAIN --> STORE
    PIPE --> OBS
    PIPE --> NS --> WHT & NATS & SLACK
    MCP & BOT & FAPI & FST --> PIPE
```

---

## Architecture — C4 (full roadmap state)

### Level 1: System Context

```mermaid
C4Context
    title System Context — job_ftch

    Person(operator, "Operator", "Configures sources, filters, sinks via YAML / CLI")
    Person(enduser, "End user", "Searches jobs, receives digests via bot or MCP client")

    System(jf, "job_ftch", "Async job ingestion engine. Fetches, normalises, deduplicates, stores, and broadcasts job postings from heterogeneous sources.")

    System_Ext(telegram, "Telegram", "MTProto channels, groups, comment threads")
    System_Ext(careersites, "Career sites", "Greenhouse, Lever, Workday, custom HTML boards")
    System_Ext(jobapis, "Official job APIs", "HH.ru, LinkedIn, Greenhouse API, Lever API")
    System_Ext(ats, "ATS / push", "Inbound webhook events from applicant tracking systems")
    System_Ext(llm, "LLM provider", "OpenAI, local models via instructor")

    System_Ext(postgres, "PostgreSQL", "Job storage, FTS index, run history")
    System_Ext(qdrant, "Qdrant", "Vector store for semantic job search")
    System_Ext(eventbus, "NATS / Redis / Kafka", "Outbound event bus")
    System_Ext(notif, "Slack / Discord / Webhook", "Notification endpoints")
    System_Ext(mcpclients, "MCP clients", "Claude Code, Cursor, Claude Desktop, OpenCode")

    Rel(operator, jf, "Configures and runs")
    Rel(enduser, jf, "Searches, subscribes, receives digest", "Telegram bot / MCP")

    Rel(jf, telegram, "Fetches messages", "Telethon MTProto")
    Rel(jf, careersites, "Crawls listings", "httpx + selectolax")
    Rel(jf, jobapis, "Calls REST APIs", "httpx")
    Rel(ats, jf, "Pushes job events", "HTTP inbound webhook")
    Rel(jf, llm, "Extracts structured fields", "instructor + openai SDK")

    Rel(jf, postgres, "Stores jobs, FTS, run history", "asyncpg")
    Rel(jf, qdrant, "Indexes and queries embeddings", "qdrant-client")
    Rel(jf, eventbus, "Publishes job events", "nats.py / aiokafka")
    Rel(jf, notif, "Sends batch notifications", "HTTP webhook")
    Rel(mcpclients, jf, "Calls tools, reads resources", "MCP stdio / SSE")
```

---

### Level 2: Containers

```mermaid
C4Container
    title Container diagram — job_ftch

    Person(operator, "Operator")
    Person(enduser, "End user")

    Container_Boundary(jf, "job_ftch system") {
        Container(cli, "CLI runner", "Python / app.py", "Assembles pipeline from Settings / TenantConfig and runs once or as daemon.")
        Container(mcp, "FastMCP server", "Python / FastMCP", "MCP protocol server. 15+ tools and job:// resources. stdio + SSE transports.")
        Container(bot, "Telegram bot", "Python / aiogram", "/search, /subscribe, /digest. Polling; optional FastAPI webhook bridge.")
        Container(fst, "FastStream worker", "Python / FastStream", "Wraps pipeline as message queue consumer/producer.")

        Container(pipeline, "Pipeline core", "Python / asyncio", "Item-by-item orchestration: Source fetch → node chain → Sink emit. RunSummary, exception handling.")
        Container(sources, "Source adapters", "Python", "Telegram (MTProto), CareerSite (HTML), Official APIs, WebhookSource, WebSocketSource, DebugSource.")
        Container(nodes, "Processing nodes", "Python", "SanitizeNode, TriageNode, DedupNode, ExtractionNode (LLM), ValidationNode, JobGroupNode.")
        Container(sinks, "Sink adapters", "Python", "JsonFileSink, SQLiteJobSink, TelegramPublishSink, NotificationSink, FanOutSink.")

        Container(store, "Store backends", "asyncpg / aiosqlite", "SQLiteStore, PostgreSQLStore. Dedup keys, run state, IncrementalCursor.")
        Container(jobbackend, "Job backends", "asyncpg / aiosqlite", "SQLiteJobBackend (FTS5), PostgreSQLJobBackend. JobPersistenceBackend protocol.")
        Container(vectorbackend, "Vector backend", "qdrant-client", "QdrantVectorBackend. EmbeddingProvider: OpenAI / SentenceTransformers.")
        Container(registry, "Extension registry", "Python / entry_points", "@register_* decorator + entry point loader. Zero core edits for new adapters.")
        Container(obs, "Observability", "prometheus-client / structlog", "PrometheusExporter, RunHistory, lineage graph, IncrementalCursor.")
    }

    System_Ext(tg_ext, "Telegram")
    System_Ext(llm_ext, "LLM provider")
    System_Ext(pg_ext, "PostgreSQL")
    System_Ext(qd_ext, "Qdrant")
    System_Ext(notif_ext, "Notification targets")
    System_Ext(mcp_ext, "MCP clients")

    Rel(operator, cli, "Runs pipeline", "CLI / env / YAML")
    Rel(enduser, bot, "Sends commands", "Telegram")
    Rel(mcp_ext, mcp, "Calls tools", "MCP stdio/SSE")

    Rel(cli, pipeline, "Assembles and triggers")
    Rel(mcp, pipeline, "Triggers runs, queries")
    Rel(bot, pipeline, "Triggers runs, queries")
    Rel(fst, pipeline, "Consumes queue messages")

    Rel(pipeline, sources, "fetch()")
    Rel(pipeline, nodes, "process()")
    Rel(pipeline, sinks, "emit()")
    Rel(pipeline, store, "dedup + run state")
    Rel(pipeline, jobbackend, "persist + search")
    Rel(pipeline, vectorbackend, "index + query embeddings")
    Rel(pipeline, obs, "metrics, lineage, run history")

    Rel(sources, tg_ext, "Telethon MTProto")
    Rel(nodes, llm_ext, "instructor / openai SDK")
    Rel(store, pg_ext, "asyncpg")
    Rel(jobbackend, pg_ext, "asyncpg")
    Rel(vectorbackend, qd_ext, "qdrant-client")
    Rel(sinks, notif_ext, "HTTP / NATS / Kafka")

    Rel(registry, sources, "Discovers and instantiates")
    Rel(registry, sinks, "Discovers and instantiates")
```

---

### Level 3: Pipeline core components

```mermaid
C4Component
    title Component diagram — Pipeline core

    Container_Boundary(app, "application/") {
        Component(orch, "Pipeline", "pipeline.py", "Async item-by-item orchestrator. Drives source → node chain → sink. Records RunSummary, handles RawItemDropped / RawItemRejected.")
        Component(contracts, "Contracts", "contracts.py", "@runtime_checkable Protocol definitions: Source, Stage[In,Out], SanitizingNode, ProcessingNode, Sink, Store, LLMProvider, StoreConnector, JobPersistenceBackend, SearchBackend, EmbeddingProvider, VectorBackend, AuthProvider, IngestMode, BypassStrategy.")
        Component(reg, "Registry", "registry.py", "Open decorator registry + entry_points loader for sources, sinks, stores, LLMs, parsers, notification targets.")
        Component(builder, "PipelineBuilder", "builder.py", "Fluent API: .source(spec).stage(node).sink(s).store(s).build() → Pipeline.")
        Component(drops, "Drops / Rejections", "drops.py / rejections.py", "Typed exception hierarchy for item-level control flow inside Pipeline.run().")
    }

    Container_Boundary(nodes_b, "nodes/") {
        Component(san, "SanitizeNode", "sanitize.py", "First gate. Max length, encoding fixes, quarantine on policy violation.")
        Component(tri, "TriageNode", "triage.py", "FilterProfile-driven pre-filter. Skips LLM call for irrelevant items.")
        Component(ded, "DedupNode", "dedup.py", "rapidFuzz near-duplicate check against processed keys in Store.")
        Component(ext, "ExtractionNode", "extraction.py", "RawItem → Job via LLM (instructor). Partial-extraction fallback via heuristics.")
        Component(val, "ValidationNode", "validation.py", "Normalise title/company/location, parse compensation, score, route borderline to review sink.")
        Component(grp, "JobGroupNode", "job_group.py", "Cross-source aggregation. Fingerprint + embedding similarity → JobGroup merge, canonical Job selection.")
    }

    Container_Boundary(dom, "domain/") {
        Component(job, "Job / RawItem", "job.py / raw_item.py", "Pydantic models. Job carries schema_version, lifecycle status, group_id, raw_item_id lineage.")
        Component(jgm, "JobGroup", "job_group.py", "Aggregated representation of the same job observed from N sources.")
        Component(cur, "IncrementalCursor", "cursor.py", "Unified watermark: last_seen_id, last_seen_at, page_token. Used by all source adapters.")
        Component(fp, "FilterProfile", "filter_profile.py", "Configurable relevance: positive_keywords, negative_keywords, required_patterns, min_score.")
        Component(nc, "NotificationConfig", "notification.py", "What/when/where to broadcast: trigger, targets, payload_format, min_score filter.")
    }

    Rel(orch, san, "process()")
    Rel(orch, tri, "process()")
    Rel(orch, ded, "process()")
    Rel(orch, ext, "process()")
    Rel(orch, val, "process()")
    Rel(orch, grp, "process()")
    Rel(orch, contracts, "Protocol types")
    Rel(builder, orch, "Constructs")
    Rel(reg, builder, "Provides factories")
    Rel(ext, job, "Produces Job")
    Rel(grp, jgm, "Produces JobGroup")
    Rel(ded, cur, "Updates watermark")
    Rel(tri, fp, "Reads FilterProfile")
```

---

## Quick Start

```bash
git clone https://github.com/[owner]/job_ftch
cd job_ftch
uv sync
cp .env.example .env
```

Run with fixture data (no credentials required):

```bash
uv run python app.py \
  --source-path fixtures/e2e/multisource_positive.jsonl \
  --output-path artifacts/debug/jobs.json \
  --max-items 20
```

Run a Telegram channel:

```bash
uv run python app.py \
  --source-backend telegram_channel \
  --telegram-entity ai_jobs \
  --max-items 100
```

Run a career site:

```bash
uv run python app.py \
  --source-backend career_site \
  --career-site-url https://job-boards.greenhouse.io/clickhouse
```

Evaluate extraction quality offline:

```bash
uv run python scripts/evaluate_extraction.py \
  --fixture fixtures/extraction/gold_samples.jsonl \
  --llm-backend heuristic
```

Outputs:
- `artifacts/debug/jobs.json` — main jobs
- `artifacts/debug/review.jsonl` — borderline items for operator review
- `artifacts/debug/rejected.jsonl` — dropped items with reasons
- `artifacts/debug/quarantine.jsonl` — items blocked at sanitize gate

---

## Documentation

| Topic | File |
|---|---|
| Architecture (detailed C4 + principles) | [docs/architecture.md](docs/architecture.md) |
| Tech stack | [docs/tech_stack.md](docs/tech_stack.md) |
| Dev rules | [docs/rules.md](docs/rules.md) |
| Roadmap | [docs/roadmap.md](docs/roadmap.md) |
| Configuration reference | [docs/configuration.md](docs/configuration.md) |
| Source setup | [docs/source_setup.md](docs/source_setup.md) |
| Examples | [docs/examples.md](docs/examples.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Release checklist | [docs/release_checklist.md](docs/release_checklist.md) |
| ADRs | [docs/adr/](docs/adr/) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
