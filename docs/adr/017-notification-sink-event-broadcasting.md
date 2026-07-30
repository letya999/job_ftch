---
title: "017 — NotificationSink: configurable event broadcasting"
description: "**Status**: PROPOSED"
updated: 2026-07-24
---
# 017 — NotificationSink: configurable event broadcasting

**Status**: PROPOSED
**Date**: 2026-06-07

> Outdated note (2026-06-12): this ADR is preserved as the original notification-broadcasting decision.
> Current normalized public contract is `JobRecord`, so references below to legacy `Job`
> should be read historically. Current architecture references:
> - [ADR-024](024-canonical-job-contract-and-matching-funnel.md)
> - [Architecture](../architecture.md)

## Context

Phase 27 addresses outbound broadcasting of job events after extraction. Existing sinks handle durable output (JSON files, SQLite) or interactive delivery (Telegram posting). Neither handles the "fire webhook to N external systems after each run" use case. The system needs:
- Multiple delivery targets (HTTP, NATS, Slack, Discord, Kafka, Redis)
- Configurable trigger modes (per-job, batched, on run completion)
- Failure isolation (one target failure must not affect others or the main pipeline)
- Operator-controlled format (full job, summary, batch, Jinja2 template)

FastStream (RM-111) wraps the entire pipeline as a message queue worker — a different pattern. This decision is about lightweight fire-and-forget broadcasting from inside the pipeline, not the queue-consumer architecture.

## Decision

`NotificationSink` implements `Sink[Job]` and integrates via `FanOutSink`. `Pipeline` is unaware of it.

**Trigger modes** (`NotificationTrigger` enum):
- `per_job` — fire immediately on each `emit(job)` call
- `batched(interval_seconds)` — accumulate in an `asyncio.Queue`, drain every N seconds via background task
- `on_count(n)` — flush when buffer reaches N items
- `on_run_complete` — `emit()` is a no-op; `flush(summary=RunSummary)` fires once

**Targets** (`NotificationTarget` discriminated union):
- `WebhookTarget(url, method, headers, hmac_secret, timeout_seconds)` — HTTP POST with optional HMAC-SHA256 `X-Hub-Signature-256` header
- `NATSTarget(subject, nats_url)` — NATS subject publish via `nats.py`
- `RedisTarget(channel, redis_url)` — Redis Pub/Sub or Streams
- `KafkaTarget(topic, bootstrap_servers)` — Kafka via `aiokafka`
- `SlackTarget(webhook_url)` — Slack incoming webhook with auto Block Kit formatting
- `DiscordTarget(webhook_url)` — Discord webhook with auto embed formatting

**Fan-out**: all targets fire concurrently via `asyncio.gather(..., return_exceptions=True)`. One target failure logs a `warn` and increments `RunSummary.notifications_failed`; it does not raise and does not affect other targets.

**Payload formats**: `full_job`, `job_summary`, `run_summary`, `batch`, `jinja_template` (Jinja2 string in config).

**Credentials**: all target secrets (bearer tokens, HMAC keys) resolved via `AuthProvider` at build time. Never stored in `NotificationConfig` YAML.

**Registration**: `@register_notification_target(name)` decorator; entry point group `job_ftch.notification_targets` for third-party targets.

## Consequences

- (+) Decoupled from pipeline: `Pipeline` never imports `NotificationSink`.
- (+) Multiple targets, multiple trigger modes, all from one YAML block in `TenantConfig`.
- (+) One target failure does not cascade to other targets or the main pipeline.
- (+) Jinja2 template mode gives operators full control over payload shape.
- (-) `batched` mode holds jobs in memory until flush; a crash before flush loses the batch (acceptable for notifications, not for persistence).
- (-) `on_run_complete` mode requires callers to invoke `flush(summary=...)` explicitly; `PipelineBuilder` must wire this automatically.
- (-) Four optional extras groups (`[nats]`, `[redis]`, `[kafka]`, Slack/Discord use only httpx) increase extras surface area.
