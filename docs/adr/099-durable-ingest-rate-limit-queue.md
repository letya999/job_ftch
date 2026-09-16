---
title: "099 — Durable ingest continuation for upstream rate limits"
description: "Durable source-run continuation for upstream rate limits."
updated: 2026-09-16
---
# ADR-099: Durable ingest continuation for upstream rate limits

## Status

Accepted

## Context

Career-site parsers can receive an authoritative `429` with a server-provided
wait. Sleeping inside the source deadline loses work on process restart and
retrying early creates another limit. The existing outbox is for delivery and
the deferred resolver is not a high-volume ingest queue.

## Decision

Persist one safe source continuation per `(tenant, run, source)` in
`jf_ingest_tasks`, with a per-scope cooldown in `jf_ingest_rate_limits`.
Workers claim tasks with leases, requeue expired leases, and resume the same
logical `run_id` after the cooldown. The task contains no browser session,
cookie, credential, or operator attachment.

The server wait is authoritative. A wait above
`JOB_FTCH_INGEST_QUEUE_MAX_WAIT_SECONDS`, or an aged-out run/max-attempts
condition, becomes `needs_operator`; the system never retries before the
server's wait. All worker and policy values are Settings/env configuration.

SQLite uses a short `BEGIN IMMEDIATE` claim transaction; PostgreSQL uses
`FOR UPDATE SKIP LOCKED`. No external broker is introduced.

## Consequences

- `RunSummary.completion_state` exposes `waiting_rate_limit` until every
  continuation completes.
- A process restart resumes ready tasks from the database.
- A single runner worker is intentionally used; database leases allow multiple
  processes to share the queue safely, while the existing tenant lock prevents
  overlapping runs for one tenant.
- `TD-015` remains open for a future independent discovery/detail work queue;
  this decision only makes source-run continuation durable.
