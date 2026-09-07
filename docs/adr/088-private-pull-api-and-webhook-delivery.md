---
title: "088 — Private pull API and optional webhook delivery"
description: "Stored tenant data is exposed through an authenticated generic API; webhooks are optional acceleration."
updated: 2026-09-06
---
# 088 — Private pull API and optional webhook delivery

**Status**: ACCEPTED  
**Date**: 2026-09-06

## Context

Downstream systems must be able to obtain everything Jobfetch stored for a time interval without Jobfetch knowing which product consumes it. Personal relevance data and vacancy results cannot be exposed through an unauthenticated endpoint. Delivery must not make ingest success depend on consumer availability.

The project already has PostgreSQL tenant storage, an outbox, delivery targets, HTTP support and API-key checks that can be consolidated instead of replaced.

## Decision

1. PostgreSQL is the source of truth. A generic `/v1` pull API exposes tenant runs, observations, decisions and job records with stable cursor pagination and time-range filters.
2. Private endpoints require `Authorization: Bearer <token>`. The first implementation uses one environment-supplied service token, compares it in constant time, fails closed when absent and applies an explicit tenant allowlist; no user accounts, OAuth server or token database is introduced.
3. Existing public `ai_jobs` behavior remains available through its current compatibility route. New tenants are private unless explicitly added to the public allowlist.
4. Consumers advance their own cursor only after committing received data. Jobfetch does not add a consumer acknowledgement entity for pull delivery.
5. A tenant may configure an HTTP webhook delivery target. It uses the existing durable outbox, repeatable pending-record recovery and a stable idempotency key; ingest and storage succeed even when delivery fails.
6. Webhook requests carry a timestamp, idempotency key and HMAC-SHA256 signature calculated over the timestamp and exact body. Secrets are environment references and never tenant YAML values or log fields.
7. Webhooks send stored accepted and review job records by default. Raw observations and rejected outcomes remain available through pull; widening push scope is explicit tenant configuration.
8. Jobfetch contracts use Jobfetch vocabulary only and contain no CareerGo-specific entity, endpoint or callback name.

## Consequences

- (+) CareerGo can backfill reliably by time range or cursor and may also receive low-latency notifications.
- (+) A lost or disabled webhook cannot lose vacancies.
- (+) Authentication protects personal tenant data without introducing an identity platform.
- (-) Static service-token rotation is operational rather than self-service.
- (-) Consumers must deduplicate webhook and pull delivery by the stable record/idempotency key.
