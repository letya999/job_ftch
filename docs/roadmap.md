# Roadmap & Status

This document tracks the high-level capabilities and implementation status of `job_ftch`. It serves as the source of truth for what has been shipped, what is experimental, and what is planned for the future.

For detailed architecture rules, see [docs/architecture.md](docs/architecture.md) and [AGENTS.md](../AGENTS.md).

## Core Capability Status

| Capability Area | Status | Components / Notes |
| :--- | :---: | :--- |
| **Core Pipeline** | **Done** | Spine, Sanitize, Triage, Dedup, Normalize, Score, Aggregate nodes. `RawItem` -> `JobDraft` -> `JobRecord` -> `JobGroup` flow. |
| **Extraction** | **Done** | LLM (instructor/OpenAI) + heuristic fallback. Validation layer. |
| **Sources: Telegram** | **Done** | Channels, Groups, Comments (polling mode). |
| **Sources: Web/RSS** | **Done** | Declarative HTML, Career-site monitors, ATS patterns (Greenhouse/Lever), RSS feeds. |
| **Sources: Official APIs** | **Partial** | Experimental adapters for HH.ru, Greenhouse, Lever, Superjob. REST API generic adapter. |
| **Sources: Advanced** | **Partial** | Browser-based (Playwright) and Hard Scrapers + Stealth/Proxy bypass (experimental/community). |
| **Storage & Backends** | **Done** | SQLite (default), PostgreSQL. FTS5 full-text search. |
| **Vector Search** | **Done** | Qdrant, pgvector. Embeddings (FastEmbed/E5/OpenAI). Cross-encoder reranking. |
| **Multi-tenancy** | **Done** | `TenantConfig`, `TenantRunner` isolation, isolated stores/sinks. |
| **NLP & Language** | **Done** | Language detection, Translation node. |
| **Observability** | **Partial** | `JobLineage`, `RunHistory`, `PrometheusExporter` (basic metrics). |
| **Runtime Adapters** | **Done** | Telegram Bot, MCP Server, PipelineBuilder (Library API), CLI. |
| **Ingestion Modes** | **Partial** | Polling, RSS, REST incremental. Webhook/WebSocket/Realtime Push (Not done). |

## Status Summary

### Done (Shipping Today)
- **Library Spine**: `PipelineBuilder`, `ProcessingNode` chain, pluggable Registry and Entry-point plugins.
- **Job Intelligence**: LLM extraction with `instructor`, heuristic triage, semantic pre-filtering, scoring, and normalization.
- **Storage**: Production-ready SQLite and PostgreSQL backends with full-text search.
- **Semantic Tier**: Vector search (Qdrant/pgvector) and local embeddings (FastEmbed).
- **Operations**: Multi-tenant daemon mode, run history persistence, and data lineage tracing.
- **Interfaces**: Fully functional Telegram bot (polling), MCP server (FastMCP), and CLI.

### Partial / Experimental
- **Official APIs**: Foundational adapters for major boards are present but require real-world hardening.
- **Observability**: Metrics are exported to Prometheus, but deep dashboarding and alerting are not yet standard.
- **Scraping Arms Race**: Browser-based scraping and stealth bypasses are experimental and community-maintained.
- **Aggregation**: Cross-source `JobGroup` aggregation is functional but requires further tuning for high-volume edge cases.

### Not Done / Future (Backlog)
- **Realtime Push**: Webhook and WebSocket ingestion for immediate low-latency updates.
- **Scale**: Realtime Telegram event listening at high volume (Telethon/EventListener).
- **Automation**: Self-healing scrapers and autonomous source discovery.
- **Ecosystem**: Unified `RuntimeAdapter` protocol and host (see [docs/techdebt.md](techdebt.md)).

## Roadmap Backlog
All unbuilt features and architectural improvements have been moved to the [Technical Debt & Backlog](techdebt.md) document, which serves as the single registry for future work.
