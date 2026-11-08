---
title: "053 — Durable outbox and delivery idempotency"
description: "**Status**: ACCEPTED"
updated: 2026-11-03
---
# 053 — Durable outbox and delivery idempotency

**Status**: ACCEPTED
**Date**: 2026-07-10

## Context

An in-process sink emit can fail or the process can stop after a policy decision
but before delivery. Marking an item processed is not a delivery guarantee.

## Decision

Terminal policy creates one immutable outbox record per destination, including
the primary sink, before delivery. Its state machine is
`DECIDED -> OUTBOXED -> DELIVERED`; failures remain retryable. Each record has a
deterministic idempotency key derived from the observation content identity,
decision version, and destination. Destinations with `emit_envelope` or
`deliver_envelope` receive that key and must deduplicate their side effect.
Legacy `emit` and `deliver` destinations remain at-least-once: a crash after
their side effect and before the outbox state write can repeat it. A successful
in-memory emit is not proof of durable delivery.

## Consequences

- Crashes between decision and sink delivery become recoverable.
- Replays cannot duplicate destinations that atomically enforce the envelope key.
  Telegram and file sinks without such enforcement can still duplicate after a
  crash at the delivery/state-write boundary.
- Removed destinations leave observable pending records and recovery reports an
  error until their configuration is restored or the records are reconciled.
- `mark_processed` remains separate from per-destination delivery state.
