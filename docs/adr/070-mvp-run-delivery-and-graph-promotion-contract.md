---
title: "ADR-070: MVP run, delivery, and graph promotion contract"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-070: MVP run, delivery, and graph promotion contract

**Status**: ACCEPTED
**Date**: 2026-07-17
**Extends**: ADR-064, ADR-066, ADR-067, ADR-069

## Context

The synchronous Telegram `/run` path previously applied relevance twice: once
in the typed graph and again in the adapter. Accepted jobs were queued for
post-accept enrichment, but no worker consumed that queue before delivery.
Graph rollout also mutated benchmarked YAML metadata, invalidating the hash
used as evaluation evidence.

## Decision

1. `DecisionNode` remains the only owner of terminal relevance. Adapters may
   enforce delivery safety and idempotency, but may not apply score thresholds.
2. Post-accept work remains represented by durable, idempotent tasks. A
   synchronous command run drains its own delivery-critical tasks before it
   queries publishable jobs. Failed tasks remain retryable; no extra worker
   container is required for the MVP.
3. An accepted record is a vacancy by contract: `ACCEPT` implies
   `post_type=job_posting`. A violation is observable as a contract error and
   is never silently converted into "no vacancies" by an adapter.
4. Runtime selects an immutable graph by path plus an exact expected hash.
   Promotion changes runtime configuration, not the benchmarked graph file.
5. One `run_id` is allocated before source preparation and correlates source
   operations, graph nodes, post-accept work, persistence, and delivery.

## Consequences

- The bot reports the graph's decision rather than a second policy.
- `/run` returns only after accepted jobs are ready for deterministic delivery.
- A graph file changed after evaluation fails fast at startup/run construction.
- OpenObserve and Langfuse can be joined by one run identifier without sharing
  their source-specific payloads.

## Validation

Clean Telegram canary `a06ca67c4bc14038938f72605a611f66` validated the
implemented contract on 2026-07-17: 42 fetched/extracted, 5 ACCEPT,
37 terminal REVIEW, 0 DEFERRED, 5 persisted and 5 bot-eligible. The exact
compiled graph hash was
`4c7e0c291dcc1439efb12cbb62edeaba33f21a82aab4b6204ffff6ef7b03d907`.
OpenObserve and Langfuse both contain the same run ID; Langfuse contains all
29 graph nodes, 42 decision spans, and five aggregation plus five enrichment
post-accept spans. Complete provider usage was 48 requests and `$0.0238476`.
