# 016 — JobGroup: cross-source aggregation

**Status**: ACCEPTED
**Date**: 2026-06-07

> Outdated note (2026-06-12): this ADR is preserved as the original aggregation decision.
> Current rollout changed the concrete contract and placement:
> 1. Source-level records are now read as `JobRecord`, not the older flat `Job`.
> 2. The shipped node is `JobAggregationNode`; exact funnel position continues to evolve with [ADR-024](024-canonical-job-contract-and-matching-funnel.md).
> 3. Current high-level architecture is tracked in [Architecture](../architecture.md).

## Context

Phase 25 addresses a fundamental problem: the same job posting appears across multiple sources. A senior ML Engineer role at Sber might be in the official HH.ru API response, reposted in three Telegram channels, and listed on the company career site. Without aggregation, the operator sees five items for one job. Naive deduplication (ADR-005) drops duplicates — but loses the information that the job was seen in five places, which is a signal of its reach and actuality.

## Decision

Introduce `JobGroup` as a first-class domain object that aggregates the same job observed from N sources into one canonical representation.

**Identity matching** is a two-stage pipeline:

1. **Fingerprint matching** (fast, zero I/O): structural hash over `(canonical_company, normalised_title, location, work_mode)`. Items with the same fingerprint are candidate matches.
2. **Embedding similarity** (for candidates only): cosine similarity of `title + company + description` embeddings. Threshold configurable in `FilterProfile`.

**Merge policy**:
- Canonical `Job` is selected from the group by priority: official API > career site > Telegram channel > Telegram group.
- Fields from lower-priority sources fill in gaps in the canonical record (e.g., a Telegram post may have salary info not present on the career site).
- `JobGroup.source_appearances: list[SourceAppearance]` preserves all raw observations (source_kind, url, first_seen_at, last_seen_at).

**Storage**: `JobGroupStore` protocol with `SQLiteGroupBackend` (default) and `PostgreSQLGroupBackend`.

**Pipeline integration**: `JobGroupNode` is inserted after `ValidationNode`. It's optional and disabled by default; enabled by `PipelineBuilder.with_job_grouping()`.

## Consequences

- (+) One canonical job record regardless of how many sources repost it.
- (+) Source spread (seen in 5 places) is a preserved signal, not discarded.
- (+) Fields from lower-quality sources fill gaps in higher-quality canonical records.
- (-) Fingerprint + embedding matching adds latency per item (embedding call for candidate pairs only).
- (-) Merge policy needs careful testing: wrong canonical selection degrades record quality.
- (-) `JobGroupStore` adds a second persistent store dependency alongside `JobPersistenceBackend`.
