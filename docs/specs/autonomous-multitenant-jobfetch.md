---
title: "Autonomous multi-tenant Jobfetch"
description: "Specification for isolated search lanes, durable high-recall storage, private pull access and optional webhook delivery."
updated: 2026-09-06
---
# Autonomous multi-tenant Jobfetch

**Decisions**: [ADR 087](../adr/087-autonomous-tenant-search-lanes.md),
[ADR 088](../adr/088-private-pull-api-and-webhook-delivery.md),
[ADR 089](../adr/089-openai-observability-in-openobserve.md)

## Goal

Jobfetch independently runs any number of isolated job-search tenants, preserves all successfully received observations, and makes stored data safely available to future consumers without depending on them.

## Scope

- Per-tenant profile, ontology, sources, graph recipe, models, schedule, concurrency and retention.
- Autonomous scheduled and manual execution.
- Durable raw observations, decision outcomes, run metrics and source outcomes.
- Authenticated pull API with time-range and cursor access.
- Optional signed webhook backed by the existing outbox.
- Separate payload-safe OpenAI telemetry in OpenObserve.
- Backward-compatible operation of `ai_jobs`.

Career CRM entities, applications, contacts, conversations, follow-ups, resume choice and company research belong to downstream systems.

## Runtime model

Tenant is the only operational scope. A profile is tenant-owned input data containing target roles, skills, anti-patterns, examples and ontology references. A graph recipe lists and configures nodes; any node, including the relevance prefilter, may be enabled, disabled, replaced or reconfigured per tenant.

For each due tenant the scheduler:

1. resolves and validates the complete tenant configuration;
2. creates a run record and configuration fingerprint;
3. invokes every enabled and due source within tenant limits;
4. stores each fetched raw observation before processing;
5. runs the tenant graph and stores its outcome;
6. writes source and run summaries;
7. enqueues configured delivery after the database transaction succeeds.

A failure in one tenant, source, pipeline item or webhook does not stop other tenants. A second run of the same tenant does not overlap an active run.

## Configuration contract

Each tenant configuration provides:

| Field | Meaning |
|---|---|
| `tenant_id` | Stable search-lane identifier. |
| `enabled` | Whether scheduled execution is allowed. |
| `profile` | Tenant-owned profile or profile artifact reference. |
| `ontology` | Tenant-owned ontology/shots artifact references. |
| `pipeline_recipe` | Graph, node settings and model selection. |
| `sources` | Source definitions and source-specific schedules/limits. |
| `schedule` | Tenant-level schedule and timezone. |
| `retention` | Raw and outcome retention policy; high-recall tenants may select unlimited retention. |
| `deliveries` | Optional existing sink targets, including HTTP webhook. |

Secrets are environment references. Configuration validation fails before a run when a referenced artifact, model setting or secret is missing. Existing common settings remain defaults so `ai_jobs` does not require a migration to run.

## Persistence and high recall

- Each observation belongs to exactly one tenant and has a stable observation id, source id, source-native id when known, an observed timestamp, fetch timestamp, raw payload and content hash.
- Re-observing the same source item is append-only at the ledger boundary and does not erase prior evidence required by the existing deduplication policy.
- Every processed observation stores its graph outcome (`accept`, `review`, `reject` or processing failure) and run id.
- A run summary records trigger (`schedule`, `api`, `cli` or `replay`), configuration fingerprint, status, counts, duration and error summary.
- A source outcome exists for every configured source: `disabled`, `not_due`, `attempted`, `rate_limited`, `budget_exhausted`, `failed` or `completed`, with fetched/stored/deduplicated/processed counts where applicable.
- Logs and webhooks are never the only copy of a fetched observation.

## Private API

All `/v1` endpoints below require a valid bearer token authorized for `{tenant_id}`:

| Method and path | Purpose |
|---|---|
| `GET /v1/tenants` | List tenants visible to the token. |
| `POST /v1/tenants/{tenant_id}/runs` | Queue a non-overlapping run; return `202` and `run_id`. |
| `GET /v1/tenants/{tenant_id}/runs` | List run summaries. |
| `GET /v1/tenants/{tenant_id}/runs/{run_id}` | Read one run and all source outcomes. |
| `GET /v1/tenants/{tenant_id}/observations` | Read raw observations by observed time range/source/cursor. |
| `GET /v1/tenants/{tenant_id}/outcomes` | Read decisions by run/outcome/cursor. |
| `GET /v1/tenants/{tenant_id}/jobs` | Read normalized job records by observed time range/cursor. |
| `POST /v1/tenants/{tenant_id}/replays` | Queue processing of stored observations through the current graph without fetching sources. |

Collections are ordered by `(observed_at, id)` and return an opaque next cursor. `limit` has a safe server default and maximum defined during implementation from measured record sizes. Invalid/expired credentials return `401`; valid credentials outside their tenant allowlist return `403`; an active same-tenant run returns `409`.

## Webhook contract

Webhook delivery is optional. The body contains contract version, event id, tenant id, run id, event type `job.upserted`, emitted timestamp, idempotency key and the stored job record. Headers contain the timestamp, idempotency key and `HMAC-SHA256` signature. Delivery uses the existing durable outbox and pending-record recovery; there is no separate dead-letter queue. Ordering is not guaranteed; duplicate delivery is allowed and must retain the same idempotency key.

## Metrics and observability

PostgreSQL keeps queryable run and source metrics. OpenObserve receives operational logs and traces correlated by tenant id and run id. OpenAI telemetry is routed separately and includes metadata, latency, token counts, retry/rate-limit state and errors, but no prompts, responses, profiles, vacancy bodies or secrets by default.

Minimum operational queries must answer:

- what each tenant ran during a time interval and why a source did or did not run;
- how many observations were fetched, stored, deduplicated and assigned to each outcome;
- which runs or sources failed or exhausted limits;
- which webhook deliveries are pending, delivered or deferred after a failed attempt;
- OpenAI latency, tokens, errors and retries by tenant, run, node and model.

## Compatibility

- The current `ai_jobs` tenant, graph, thresholds, sources, publishing and schedule remain unchanged unless its configuration is explicitly edited.
- Existing public `ai_jobs` routes keep their behavior; new tenants are absent from the public allowlist.
- Existing single-tenant CLI and Telegram publishing continue to use the same runner and stored results.
- No new broker, scheduler framework, ORM, workflow engine, LangFuse integration or consumer-specific entity is introduced.

## Acceptance criteria

1. Two tenants with different profiles, sources, graphs and schedules run independently and store/query only their own data.
2. A due enabled tenant runs without Telegram publishing or a downstream consumer configured.
3. Every successfully fetched item is stored before its pipeline decision; a node or webhook failure cannot remove it.
4. Every configured source has a terminal outcome in its run, including disabled and skipped sources.
5. Pulling a closed time interval through cursor pagination returns every matching stored record exactly once within that traversal, with stable ordering.
6. Missing/invalid tokens cannot read data; a valid token cannot access an unauthorized tenant; secrets and payload contents do not appear in default logs.
7. Repeated or delayed webhook attempts keep the same idempotency key and never change ingest success.
8. A replay performs no source fetch and stores a new run linked to the replayed observations and current configuration fingerprint.
9. OpenAI operational data is queryable separately in OpenObserve and correlates to the PostgreSQL run id.
10. Existing `ai_jobs` regression and architecture gates pass without configuration or behavioral regression.

## Open implementation values

- API page default and maximum, derived from measured payload sizes.
- Tenant-level retention values and database capacity alert threshold.
- Webhook retry count/backoff, using the existing outbox defaults unless production evidence requires tenant overrides.
