---
title: "108 — Observation continuations and publication backlog"
description: "Resume pending CLIProxy work from immutable observations and track accepted publication candidates independently of fetch timestamps."
updated: 2026-11-09
---
# 108 — Observation continuations and publication backlog

**Status**: ACCEPTED
**Date**: 2026-11-09

## Context

ADR 106/107 preserve HTTP results, but scheduled continuation still fetches every
source again. An accepted vacancy fetched before an operator hold can also fall
outside the resumed publication window. Attempt counters repeat accepted items
and therefore do not measure unique publications.

## Decision

- Within the existing tenant run lock, checkpoint immutable observation references
  for CLIProxy logical runs in the existing store. Preserve the pinned run and
  request identity. Drain acquired inputs into the ledger after LLM backpressure;
  resume these inputs before fetching sources again. Missing/corrupt references
  fail visibly rather than silently discarding pending work.
- Finalize parent dedup leases after all fan-out children settle. A pending child
  releases its parent; only a fully terminal fan-out consumes the parent identity.
- Record accepted job IDs in an append-only store set before marking inputs
  processed. Publication reads this backlog independently of fetch timestamps.
  A target-scoped handled set records confirmed sends, known duplicates and
  explicit terminal rejections. Existing Telegram outbox reconciliation remains
  authoritative for unknown delivery outcomes.
- Reuse store sets, run state, observation ledger and existing delivery outbox;
  add no service, dependency or credential storage. Direct LLM profiles do not
  enable CLIProxy observation continuation.
- Distinguish attempt work, replayed observations, unique accepted candidates,
  confirmed publications and deferred work in operational reports. LLM admission
  waits retain incomplete status without incrementing processing-error counts.
- T-Bank's registered source selects a packaged public CA from the bank's
  HTTPS-published certificate instructions. Listing, assessment and detail HTTP
  clients retain hostname/certificate verification; other sources retain the
  default trust bundle. No system trust store or LLM transport is changed.

## Verification

Exercise restart with pending observations and no second source fetch, content
identity across retries, partial fan-out lease release, direct-profile isolation,
and accepted publication outside the current time window. Keep duplicate delivery
and uncertain-send tests. Audit the production outage window using accepted IDs
and confirmed target receipts before backfilling any missing candidates.

## Consequences

The backlog is durable and independent of a timestamp watermark. Source fetch
failures remain separate from continuation; queued work cannot increase provider
quota. Store sets retain compact identities, while raw content stays in the
existing observation ledger. Tenant serialization remains required for checkpoint
updates, including across processes.
