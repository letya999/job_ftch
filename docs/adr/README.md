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
4. [004-pipeline-outcomes-and-stage-transition.md](004-pipeline-outcomes-and-stage-transition.md) — Use structured pipeline outcomes
5. [005-postgresql-store-first.md](005-postgresql-store-first.md) — Use PostgreSQL as the first production persistent store
