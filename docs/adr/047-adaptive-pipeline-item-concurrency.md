---
title: "ADR-047: Adaptive Runtime Concurrency"
description: "Status: ACCEPTED"
updated: 2026-07-24
---
# ADR-047: Adaptive Runtime Concurrency

Status: ACCEPTED
Date: 2026-06-28

## Context

After Phase 7B, `Pipeline.run()` can process items concurrently, but a single
fixed concurrency value is a poor fit across runs:

- small runs with 1 source do not benefit from very high item concurrency;
- multi-source runs can safely overlap more I/O-bound item work;
- different source kinds (`local_fixture`, `rss_feed`, `career_site`, `browser`)
  have very different runtime cost profiles;
- effective concurrency should respect the host's available CPU budget and, for
  item workers, the configured backend pool capacity.

We need a safe runtime strategy that improves throughput without changing
pipeline contracts or introducing a second orchestration model.

## Decision

The following settings remain operator-facing upper bounds:

- `source_fetch_concurrency`
- `source_preparation_concurrency`
- `pipeline_item_concurrency`

Each gets a matching `*_adaptive` boolean flag that allows the runtime to
resolve a lower effective value per run.

The resolver uses only stable local signals already available in the runtime:

- source count for the current run;
- weighted source work units derived from source kind;
- detected CPU count;
- configured `store_pool_max` for item workers.

Source kinds are weighted so heavy site scraping receives a larger budget than
light local or feed-like sources. Example weights:

- `local_fixture`, `telegram_*`, `rss_feed`, `lever`: `1.0`
- `rest_api`: `1.5`
- `declarative_html`: `2.0`
- `career_site`: `3.0`
- `browser`: `4.0`

Effective runtime caps:

- fetch/preparation:
  `min(requested_cap, max(1, source_work_units), max(1, cpu_count * 2))`
- item workers:
  `min(requested_cap, max(4, source_work_units * 2), max(4, cpu_count * 4), max(4, store_pool_max))`

If adaptivity is disabled, the effective value is exactly the requested cap.

Resolution happens in composition roots:

- `run_pipeline_from_settings()` for single-run/library execution;
- `TenantRunner._build_runtime_builder()` for tenant runs using effective
  sources after rate-limit/pause/probe filtering;
- `TenantRunner.run_tenant()` for source preparation concurrency.

Resolved values are persisted in run state for observability:

- `pipeline.source_fetch_concurrency`
- `pipeline.source_preparation_concurrency`
- `pipeline.item_concurrency`

## Consequences

- Default behaviour stays stable because the default requested caps remain `4`.
- Raising concurrency above `4` no longer blindly oversubscribes small runs.
- Heavy source mixes can scale up more aggressively than lightweight feeds.
- Multi-source I/O-heavy runs can scale up automatically when the host and pool
  settings allow it.
- The strategy stays simple, deterministic, and stdlib-only; no `psutil` or
  platform-specific memory probes are required.
