---
title: "ADR 018: Job Catalog and Search Architecture"
description: "ACCEPTED"
updated: 2026-07-24
---
# ADR 018: Job Catalog and Search Architecture

## Status
ACCEPTED

## Context
As the system evolves to persist and search through aggregated job listings, we need a unified approach to storing and retrieving `Job` and `JobGroup` entities, while ensuring the search mechanism is robust and consistent. We need both SQLite (for local/embedded use) and PostgreSQL (for production/scale) as backends.

## Decision
* `JobGroup` is the aggregate root for search results.
* Search operations must return `list[JobGroup]`.
* Job persistence, group persistence, and the search projection must stay strictly consistent. 
* SQLite and PostgreSQL job backends will expose the exact same application-level surface.
* Vector search is treated as optional infrastructure, and is not part of the core domain model or standard text search.

## Consequences
* Applications consuming search results will always work with `JobGroup`s, ensuring deduplication is respected.
* We must ensure atomic updates or strict consistency guarantees when writing a `Job` to both its persistence table and the full-text search index (FTS).
* The domain model remains pure, unaware of vector-specific properties or full-text representations.
