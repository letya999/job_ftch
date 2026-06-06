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
1. [001-hexagonal-architecture.md](001-hexagonal-architecture.md) — Use Hexagonal Architecture
2. [002-ddd-lite.md](002-ddd-lite.md) — Use DDD Lite
3. [003-input-quarantine-flow.md](003-input-quarantine-flow.md) — Use explicit input quarantine flow
4. [004-pipeline-node-contracts-and-stats.md](004-pipeline-node-contracts-and-stats.md) — Use typed pipeline node contracts and per-source stats
5. [005-raw-item-identity-and-dedup.md](005-raw-item-identity-and-dedup.md) — Use stable raw identity and dedup explainability
6. [006-typed-pipeline-stages.md](006-typed-pipeline-stages.md) — Support typed pipeline stage transitions
7. [007-extension-registry-and-plugin-discovery.md](007-extension-registry-and-plugin-discovery.md) — Use extension registries and plugin discovery
8. [008-declarative-career-site-extraction.md](008-declarative-career-site-extraction.md) — Use declarative career-site extraction configs
9. [009-sink-fanout-and-routing.md](009-sink-fanout-and-routing.md) — Use sink fan-out and routing adapters
10. [010-postgresql-store-first.md](010-postgresql-store-first.md) — Use PostgreSQL as the first production persistent store
