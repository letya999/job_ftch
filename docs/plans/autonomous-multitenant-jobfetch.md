---
title: "Implementation plan — autonomous multi-tenant Jobfetch"
description: "Minimal sequence for implementing ADRs 087–089 without changing ai_jobs behavior."
updated: 2026-09-06
---
# Implementation plan — autonomous multi-tenant Jobfetch

**Specification**: [Autonomous multi-tenant Jobfetch](../specs/autonomous-multitenant-jobfetch.md)

## Delivery rule

Implement one vertical slice at a time. Reuse the tenant runner, PostgreSQL stores, run statistics, outbox, HTTP client, OpenTelemetry and OpenObserve. Add no dependency and no CareerGo-specific type.

## Phase 1 — Lock compatibility

1. Add a characterization test for the resolved `ai_jobs` tenant: sources, graph order/configuration, schedule and publishing settings.
2. Add a two-tenant isolation test around the existing tenant runner and stores.
3. Capture the current architecture, unit and production-recipe regression gates as the baseline.

Exit: current behavior is executable as a regression test before runtime changes.

## Phase 2 — Make tenant configuration complete

1. Extend tenant configuration with profile/ontology references, pipeline recipe, retention and optional deliveries while retaining common-setting fallbacks.
2. Resolve and validate one immutable configuration snapshot/fingerprint at run start.
3. Build each tenant's pipeline from its recipe; keep prefilter as a normal node.
4. Allow unlimited operational-outcome retention only when a tenant explicitly selects it.

Exit: two tenants resolve different graphs and artifacts; unchanged `ai_jobs` resolves to the baseline snapshot.

## Phase 3 — Autonomous scheduling and complete run truth

1. Move the due-tenant loop outside Telegram publishing eligibility; publishing remains an optional delivery.
2. Reuse the existing same-tenant run guard and global concurrency limits.
3. Create source-outcome rows for every configured source, including skipped terminal states.
4. Persist run trigger, configuration fingerprint and aggregate fetched/stored/deduplicated/decision counts using existing run-stat extension fields where possible; migrate schema only for values that must be indexed.

Exit: a tenant with no Telegram channel runs on schedule, and its run explains every source.

## Phase 4 — Private pull API

1. Consolidate authentication into one bearer-token dependency with constant-time comparison and tenant allowlists loaded from environment configuration.
2. Add read queries and opaque `(observed_at, id)` cursor encoding over existing stores.
3. Add `/v1` tenant, run, observation, outcome and job endpoints.
4. Add asynchronous run and replay endpoints by invoking the existing runner; do not add a queue product.
5. Preserve the current public `ai_jobs` compatibility routes and verify new tenants are not exposed there.

Exit: an authorized consumer can backfill a time interval; authorization and cross-tenant tests pass.

## Phase 5 — Optional webhook

1. Add one self-registering HTTP delivery target using the installed HTTP client and existing durable outbox.
2. Serialize the versioned `job.upserted` envelope and sign timestamp plus exact body with HMAC-SHA256 from the standard library.
3. Propagate stable idempotency keys through retries and expose the existing pending/delivered/deferred outbox state.

Exit: a failing receiver does not fail ingest; retrying delivery is verifiably idempotent and signed.

## Phase 6 — OpenAI telemetry separation

1. Replace active-runtime LangFuse terminology with provider-neutral/OpenAI telemetry names while leaving historical ADRs intact.
2. Route OpenAI metadata events to a dedicated OpenObserve stream/dataset using the existing OpenTelemetry path.
3. Verify payload capture is off by default and add redaction tests for prompts, responses, profiles, vacancy bodies, secrets and headers.

Exit: OpenAI latency/tokens/errors are independently queryable and correlate with tenant/run/node; sensitive payload tests pass.

## Verification and rollout

For every phase run the smallest targeted tests, then:

1. `just architecture-verify`
2. the repository local quality gate documented in `docs/operations/ci-cd.md`
3. production recipe/regression gates from `docs/recipes/pipeline_recipe.md`
4. `ai-repo-safety scan --target .` before commit

Roll out one new non-public tenant with webhook disabled, validate storage and metrics, enable pull access, then enable webhook. Roll back by disabling that tenant or delivery; `ai_jobs` stays on its unchanged compatibility configuration throughout.
