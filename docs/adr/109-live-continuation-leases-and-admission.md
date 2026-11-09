---
title: "109 — Live continuation leases and admission"
description: "Renew safe unfinished CLIProxy runs, admit independent work and keep source acquisition live while inference waits."
updated: 2026-11-10
---
# 109 — Live continuation leases and admission

**Status**: ACCEPTED
**Date**: 2026-11-10

## Context

A production continuation outlived its initial 24-hour logical-run deadline.
It remained operator-held forever even though all HTTP outcomes were known.
The first deferred item also prevented later inputs from reaching cheap filters
or filling the bounded PostgreSQL queue. Draining the entire checkpoint before
another source acquisition starved fresh sources.

## Decision

- Treat the logical-run deadline as a renewable lease for safe unfinished work.
  Resume the same run and request identities only with unchanged routes and no
  uncertain, cancelled or non-retryable failed requests. Explicit operator holds,
  authorization failures and route changes remain held. Individual request TTLs,
  account pauses and admission budgets are not extended or reset.
- Protected asynchronous HTTP requests return deferred immediately while pending.
  Continue independent pipeline items after a deferred item; the existing bounded
  queue and account-wide worker pacing own backpressure. Preserve checkpoints and
  release provisional dedup claims on every deferred item.
- Successful responses reconcile token-based spacing to actual usage while
  retaining minimum call spacing and jitter. Compare the exact reserved deadline
  before adjusting it so a subsequently installed cooldown cannot be shortened.
- Revisit source acquisition at the normal tenant schedule while a checkpoint
  exists. Store the last acquisition time alongside the checkpoint. Between
  acquisitions replay saved observations without network refetch. Consume oldest
  observations first so newly acquired work cannot indefinitely displace them.
  This supersedes ADR 108's requirement to empty the checkpoint before refetching.
- Keep direct/inline LLM routes unchanged and add no service or dependency.

## Verification

Use disposable PostgreSQL to cross the logical-run deadline and verify stable
session/request identity, preserved results, and operator holds. Exercise pending
items beside terminal items, queue capacity and restart. Verify that acquisition
becomes due without losing existing observation references or refetching on each
short retry. Verify real publication receipts and the original checkpoint's
remaining identities after deployment; container health alone is insufficient.
