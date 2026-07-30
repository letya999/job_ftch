---
title: "053 — Durable outbox and delivery idempotency"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 053 — Durable outbox and delivery idempotency

**Status**: ACCEPTED
**Date**: 2026-07-10

## Context

An in-process sink emit can fail or the process can stop after a policy decision
but before delivery. Marking an item processed is not a delivery guarantee.

## Decision

Terminal policy creates an immutable outbox record before delivery. Its state
machine is `DECIDED -> OUTBOXED -> DELIVERED`; failures remain retryable.
Every sink receives a deterministic idempotency key derived from the observation
content identity and decision version. Sink implementations must not use a
successful in-memory emit as proof of durable delivery.

## Consequences

- Crashes between decision and sink delivery become recoverable.
- Replays do not duplicate Telegram posts or file/database output.
- Pipeline/sinks need a dedicated persistent outbox store in the next Phase 1.4
  increment; existing `mark_processed` remains transitional.
