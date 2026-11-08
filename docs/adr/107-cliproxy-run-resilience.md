---
title: "107 — CLIProxy run sessions and resilient admission"
description: "CLIProxy-only durable run affinity, paced admission, classified waits and bounded run reports."
updated: 2026-11-08
---
# 107 — CLIProxy run sessions and resilient admission

**Status**: ACCEPTED
**Date**: 2026-11-08

## Context

ADR 106 supplies optional generic PostgreSQL dispatch. Additional traffic shaping
must apply only to explicitly identified CLIProxy routes, including mixed runtimes
where other bindings use a direct compatible API. HTTP status alone does not
distinguish provider overload, quota exhaustion, account verification and unknown
inference outcomes. Deferred observations must not become heuristic successes.

## Decision

- A profile explicitly declares `gateway: cliproxy`; the new protection requires
  queued execution. Direct profiles retain ADR 106 behavior and import isolation.
- PostgreSQL owns a logical run session, route revision, scheduling offset and
  completion report. Waiting and process restarts reuse the same run. Completed
  runs get a new identity. Each item retains independent request identity and
  context; affinity never appends other vacancies to its conversation.
- Reserve account-wide start times with nonnegative jitter and uniform call/token
  pacing. Persist scheduled offsets once. Admission and cooldown waits consume no
  HTTP attempt and hold no worker. Account budgets survive run boundaries.
- Normalize structured Google/CLIProxy errors and reset deadlines. Respect every
  applicable retry lower bound, preserve long waits and distinguish model outage
  from account/authorization problems. Unknown results require operator review.
- Add a PostgreSQL provider-scope/model circuit with one serialized recovery
  attempt. Keep account pauses, rate limits and provider health independent.
- Reuse bounded HTTP clients. Preserve CLIProxy's existing OAuth singleflight;
  never add competing authentication jobs. All required egress fails closed.
- Hold observations on admission overflow, expired results and operator-required
  responses. They must neither commit source cursors nor enter partial extraction.
- Scheduling policy changes do not invalidate request content. Route/account
  generation changes require explicit handling of an unfinished pinned run.
- Optionally enqueue one idempotent, bounded run analysis using aggregate counters
  only. It uses the same account/session and budget. Report failure cannot restart
  ingestion. No deliberate typos, unrelated camouflage traffic or idle generations.
- Export low-cardinality dispatch metrics and operator-safe transition logs;
  retain terminal summaries and expose queue/run states through the existing CLI.

## Verification

Keep generic dispatch/inline contracts. Add real disposable-PostgreSQL cases for
cross-worker pacing, durable sessions and waits, route changes, circuit recovery,
capacity backpressure, report deduplication and schedule-only changes. Add SDK/node
and pipeline checks proving operator errors cannot become partial/processed items.
Probe home Internet through the selected exit without inference or OAuth payloads.
Physical phone/reboot soak is an operational check, never inferred from unit tests.

## Consequences

No new dependency or queue service is required. Additional inference is limited
to useful optional reports and real schema corrections. Local reliability does
not confer additional provider quota, supported third-party access or ban immunity.
