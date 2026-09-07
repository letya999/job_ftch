---
title: "089 — OpenAI observability in OpenObserve"
description: "OpenAI operational telemetry is separated from ingest logs without adding LangFuse or another backend."
updated: 2026-09-06
---
# 089 — OpenAI observability in OpenObserve

**Status**: ACCEPTED  
**Date**: 2026-09-06

## Context

Operators need to inspect OpenAI latency, token use, model selection, cost attribution and failures separately from source and pipeline logs. The system already uses OpenTelemetry and OpenObserve; LangFuse has been removed and must not return through documentation or dependencies.

Prompts, responses, profiles and vacancy bodies may contain personal or sensitive data and are not required for operational metrics.

## Decision

1. OpenObserve remains the only external observability backend. No new telemetry library or service is added.
2. OpenAI calls emit structured telemetry to a dedicated OpenObserve stream or equivalent dedicated dataset selected by existing OpenTelemetry routing.
3. Each event includes timestamp, tenant id, run id, node, provider, model, attempt, status, latency, input/output/total tokens, rate-limit/retry data and provider request id when available.
4. Prompt text, response text, vacancy text, profile content, credentials and authorization headers are excluded by default. Diagnostic payload capture remains disabled in production.
5. Business truth stays in PostgreSQL: run/source outcomes and stored observations are not reconstructed from telemetry.
6. Legacy LangFuse naming and documentation are removed only where they describe the active runtime; historical ADRs remain historical.

## Consequences

- (+) OpenAI operations can be queried and alerted independently without a second observability stack.
- (+) Default telemetry is useful without leaking application payloads.
- (-) Semantic debugging from full prompts and responses requires an explicit, time-bounded diagnostic procedure outside the default path.
