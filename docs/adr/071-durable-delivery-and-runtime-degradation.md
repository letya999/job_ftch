---
title: "ADR-071: Durable delivery and observable runtime degradation"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# ADR-071: Durable delivery and observable runtime degradation

**Status**: ACCEPTED
**Date**: 2026-07-17
**Extends**: ADR-070

## Context

Accepted vacancies cross several asynchronous boundaries: aggregation, output
routing, durable outbox persistence, and optional Telegram delivery. Treating
a missing route or a posting failure as success makes the outbox and run
summary contradict the externally observable result.

## Decision

1. A `RoutingSink` without a matching route must raise; it may not silently
   discard an item.
2. Posting is a durable delivery target, not a secondary/best-effort sink.
   Its failure propagates to `Pipeline`, leaving its outbox entry pending.
   Delivery targets are explicit composition-root registrations with stable
   IDs. Ordinary artifact sinks (JSON, review) are not outbox targets.
   Each registered target is acknowledged separately; recovery replays only
   the pending target named by the outbox record.
3. In-process aggregation serializes its lookup/create sequence. Persistent
   backends remain responsible for cross-process identity uniqueness.
4. Runtime fallbacks that preserve availability but weaken correctness must
   emit structured diagnostics. Snapshot and vector-search fallbacks are
   observable; private-network URL probes are refused before a request.

## Consequences

- A failed posting can be retried through the existing pending-outbox recovery
  use case instead of being recorded as delivered.
- An invalid routing decision becomes a visible item failure rather than data
  loss.
- Deployment still needs a database-level idempotent upsert before multiple
  independent runtime processes can aggregate concurrently.

## Validation

- Concurrent identical records through one aggregation node produce one group.
- An unmatched `RoutingSink` raises.
- Reachability validation rejects link-local metadata URLs.
