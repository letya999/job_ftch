---
title: "ADR-069: Split operational and ML observability"
description: "**Status:** ACCEPTED"
updated: 2026-07-24
---
# ADR-069: Split operational and ML observability

**Status:** ACCEPTED
**Date:** 2026-07-16

## Context

The MVP has two different observability jobs:

- source ingestion needs searchable structured logs and a small set of operational metrics;
- the source-agnostic ML/LLM/RAG funnel needs graph traces, node parameters, conversion, quality, latency, token usage, and usage-priced model cost.

The former Prometheus exporter exposed only end-of-run counters and required a separate dashboard stack. Langfuse is already deployed separately and is intentionally specialized for the second job.

## Decision

1. Use OpenObserve OSS in single-node local mode for operational logs and metrics.
2. Run OpenObserve as a standalone service under `deploy/observability`, with paired dev/prod compose and env templates. It owns its own persistent volume and is not part of any runtime adapter deployment.
3. Keep the existing Langfuse compose project separate. Do not embed OpenObserve into Langfuse, share Langfuse storage, or put either service in the bot container.
4. Send source acquisition, scraping, parsing, retries, failures, and source throughput to OpenObserve through direct OTLP/HTTP. Send source-independent pipeline and ML/LLM/RAG spans to Langfuse.
5. Correlate OpenObserve, Langfuse, and PostgreSQL records with the same `run_id`, `tenant_id`, `source_id`, and `item_id` where applicable.
6. Remove the custom Prometheus exporter, its HTTP port, and its optional dependency. New operational metrics use OpenTelemetry/OTLP rather than a project-specific exporter protocol.
7. Keep PostgreSQL as the authority for business run history. For each OpenAI call, store provider-reported input, cached-input, and output tokens and calculate the standard on-demand cost from a dated public pricing snapshot. Never substitute another model's tariff. The OpenAI Costs API/invoice remains the accounting authority because it can include billing adjustments and cannot be reliably joined to an individual run from a normal project key. Observability systems are diagnostic views, not business storage.
8. Bind canonical `source_id` and `source_kind` around every child source fetch,
   and promote `run_id`, tenant, graph, and source identity to searchable log
   fields. Do not retain parallel legacy source-health key formats.
9. In Langfuse, emit one run trace with graph-node spans, node parameters,
   execution outcomes, terminal statuses/reasons, final funnel counters, actual
   provider usage/cost, and delivery spans. Sink-attempt counters must not be
   labelled as final accepts.

## Consequences

- The MVP adds one optional container, not a Prometheus/Grafana/collector stack.
- Runtime-adapter deployments and restarts do not affect observability data.
- Langfuse upgrades remain isolated from operational telemetry.
- A collector may be introduced later only if direct OTLP export proves insufficient.
- One run can be joined across PostgreSQL, OpenObserve, and Langfuse without
  sharing source-specific payloads with the ML trace store.

## Validation

The 2026-07-17 clean canary used one source run id
`7682050f072d45b58762ff43da38c1f4`. OpenObserve returned 40 structured log
rows for that id and exposed the `job_ftch_*` metric streams. Langfuse returned
the same run id and graph hash, 368 decision spans, terminal conversion
`56 accept / 297 review / 15 unresolved`, and exactly 56 aggregation plus 56
enrichment spans. The authoritative final span reports 368 fetched, 353
decision-ready, 56 accepted, 0 failed, 367 LLM requests, and `$0.200855`.

That paid run predates the final custom-parser retry deduplication and is kept
as cost/correlation evidence, not as a source-count baseline. The final
source-only audit covers all 14 active sources: 216 unique non-empty vacancy
observations, no duplicate IDs/URLs, no source failure/partial result, and no
unknown/spam/announcement post type. New operational metrics use the canonical
full `kind:name` source ID, while Langfuse graph spans include node index,
versioned parameters, execution outcome, terminal status, and terminal reasons.
Delivery uses separate `routing_accepted`, `persisted_candidates`,
`eligible_to_send`, `chat_sent`, and `channel_posted` counters.

This ADR supersedes the Prometheus-specific part of ADR-040 and narrows ADR-043: Langfuse remains primary for ML/LLM/RAG observability, not source-ingestion operations.
