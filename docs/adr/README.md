# Architecture Decision Records

This directory contains ADRs (Architecture Decision Records) for job_ftch.

## Format
File: `NNN-short-slug.md`
Example: `001-hexagonal-architecture.md`

## Template
```markdown
# NNN — Title

**Status**: PROPOSED | ACCEPTED | DEPRECATED
**Date**: YYYY-MM-DD

## Context
What is the problem or situation?

## Decision
What was decided?

## Consequences
What are the trade-offs?
```

## Index

### Phases 0-10 (implemented)

1. [001-hexagonal-architecture.md](001-hexagonal-architecture.md) — Hexagonal Architecture (Ports & Adapters)
2. [002-ddd-lite.md](002-ddd-lite.md) — DDD Lite (Entity, Value Object, Repository)
3. [003-input-quarantine-flow.md](003-input-quarantine-flow.md) — Input quarantine side-channel
4. [004-pipeline-node-contracts-and-stats.md](004-pipeline-node-contracts-and-stats.md) — Node contracts and RunSummary stats
5. [005-raw-item-identity-and-dedup.md](005-raw-item-identity-and-dedup.md) — RawItem identity and deduplication
6. [006-typed-pipeline-stages.md](006-typed-pipeline-stages.md) — Typed Stage[In, Out] composition
7. [007-extension-registry-and-plugin-discovery.md](007-extension-registry-and-plugin-discovery.md) — Open registry and entry points
8. [008-declarative-career-site-extraction.md](008-declarative-career-site-extraction.md) — Declarative career site HTML parsing
9. [009-sink-fanout-and-routing.md](009-sink-fanout-and-routing.md) — Sink fan-out and routing
10. [010-reliability-and-recovery-policies.md](010-reliability-and-recovery-policies.md) — Reliability and recovery policies

### Phases 11-27 (current ADR set)

11. [011-source-spec-auth-provider.md](011-source-spec-auth-provider.md) — SourceSpec + AuthProvider separation (`ACCEPTED`)
12. [012-store-connector-protocol.md](012-store-connector-protocol.md) — StoreConnector protocol hierarchy (`ACCEPTED`)
13. [013-filter-profile-configurable-relevance.md](013-filter-profile-configurable-relevance.md) — FilterProfile: configurable relevance (`ACCEPTED`)
14. [014-search-embedding-vector-protocol-stack.md](014-search-embedding-vector-protocol-stack.md) — Search, embedding, and vector protocol stack (`ACCEPTED`)
15. [015-ingestion-mode-bypass-strategy.md](015-ingestion-mode-bypass-strategy.md) — IngestMode and BypassStrategy protocols (`ACCEPTED`)
16. [016-job-group-cross-source-aggregation.md](016-job-group-cross-source-aggregation.md) — JobGroup: cross-source aggregation (`ACCEPTED`)
17. [017-notification-sink-event-broadcasting.md](017-notification-sink-event-broadcasting.md) — NotificationSink: configurable event broadcasting (`ACCEPTED`)
18. [018-job-catalog-and-search-architecture.md](018-job-catalog-and-search-architecture.md) — Job Catalog and Persistent Search architecture (`ACCEPTED`)
19. [019-embeddings-and-vector-storage-boundary.md](019-embeddings-and-vector-storage-boundary.md) — Embeddings and Vector Storage boundary (`ACCEPTED`)
20. [020-registry-fallback-named-backend.md](020-registry-fallback-named-backend.md) — Registry fallback named backend (`ACCEPTED`)
21. [021-career-site-monitor-scraper-split.md](021-career-site-monitor-scraper-split.md) — Career-site monitor and scraper split (`PROPOSED`)
21b. [021-module-boundary-enforcement.md](021-module-boundary-enforcement.md) — Module boundary enforcement gate (`ACCEPTED`)
22. [022-cloakbrowser-advanced-bypass.md](022-cloakbrowser-advanced-bypass.md) — CloakBrowser advanced bypass (`ACCEPTED`)
23. [023-adaptive-scraping-escalation.md](023-adaptive-scraping-escalation.md) — Adaptive scraping escalation (`PROPOSED`)
24. [024-canonical-job-contract-and-matching-funnel.md](024-canonical-job-contract-and-matching-funnel.md) — Canonical job contract and top-tier matching funnel (`ACCEPTED`)
