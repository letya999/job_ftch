---
title: "072 — Career-site deadline and global work budgets"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 072 — Career-site deadline and global work budgets

**Status**: ACCEPTED
**Date**: 2026-07-19
**Extends**: [021-career-site-monitor-scraper-split.md](021-career-site-monitor-scraper-split.md), [047-adaptive-pipeline-item-concurrency.md](047-adaptive-pipeline-item-concurrency.md)

## Context

The dynamic source pool previously timed out the forwarding task while a
career-site producer could continue detail and browser work in the background.
At the same time, each active source owned its own detail concurrency, so a
large batch could multiply the configured per-source budget into hundreds of
in-flight operations.  These behaviours made source outcomes non-repeatable
and let queue waiting accidentally extend a source's wall-clock budget.

The pipeline's relevance and evidence nodes own job-content filtering.  Ingest
may only make bounded technical decisions about which listing/detail locators
to fetch.

## Decision

1. `source_hard_deadline_seconds`, `source_soft_deadline_seconds`, and bounded
   cancellation grace are operator settings.  The soft deadline moves a source
   to the overflow queue; it is not a failure by itself.
2. Every dynamic source receives one absolute monotonic deadline at start.
   Fast-lane time, overflow queue waiting, retries, and bounded browser waits
   consume that same budget.
3. A hard deadline cancels and joins the producer before its terminal snapshot
   is published.  A second cancellation is used only to complete a generator
   that consumed the first cancellation while unwinding.
4. Detail requests and browser sessions use separate loop-local global limits,
   in addition to the existing per-source detail cap.  No new orchestration
   service or dependency is introduced.
5. Generic discovery ranks only technical detail-URL likelihood before applying
   its bounded candidate cap.  It neither evaluates vacancy relevance nor
   changes downstream evidence decisions.
6. Source results preserve a primary terminal outcome and a separate deadline
   flag, so protection and deadline observations are not overwritten by report
   heuristics.

## Consequences

- Runs are bounded by `hard deadline + cancellation grace`, rather than by
  detached background cleanup.
- Operators can increase the deadline through environment/runtime settings and
  retain the overflow lane for slow but otherwise healthy sources.
- Global limits reduce rate-limit amplification and browser contention.
- A site that merely produces no confirmed postings is reported as
  `unconfirmed_empty`, never as proof that no vacancies exist.
- The detail protection circuit breaker is source-local and only stops further
  detail attempts after repeated protected outcomes; ATS/parser registration
  remains the registry's responsibility.
