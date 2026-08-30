---
title: "082 — Operator bypass pin and sweep"
description: "**Status**: ACCEPTED"
updated: 2026-08-19
---
# 082 — Operator bypass pin and sweep

**Status**: ACCEPTED
**Date**: 2026-08-19
**Extends**: [081-operator-browser-listing-probe.md](081-operator-browser-listing-probe.md),
[037-adaptive-scraping-escalation-policy.md](037-adaptive-scraping-escalation-policy.md)

## Context

MCP could inspect the bypass inventory and run one adaptive ingest, but it
could not pin a mechanic for one call or walk the ladder. Operators therefore
could not tell whether a site failed on HTTP, fingerprint/challenge, empty
listing, or parser/zero-yield.

## Decision

1. `TenantRunner.run_tenant` accepts `bypass_override` and
   `ignore_schedule_gates`. A pin copies the source spec with that `bypass`
   for this run only. Operator probes skip rate-limit / pause gates so a
   sweep can execute more than once.
2. `bypass=None` / `auto` / `adaptive` keeps the standard adaptive ladder.
   Any other registered name is that mechanic only (no further escalation).
3. Browser pins with a listing URL use the Slice 5 listing probe. HTTP pins
   and sources without a listing URL use source-scoped ingest.
4. `run_source_escalation(strategy="all")` walks `fallback_order` (capped),
   records per-route status, challenge, listing cards, fetch/yield, and
   `zero_reason` / parse diagnosis. `max_tier` truncates the ladder
   inclusively.
5. MCP still does not import browser clients.

## Consequences

- (+) Operator can run the standard flow or one mechanic and compare outcomes.
- (+) Parse vs challenge vs empty fetch is visible without reading raw logs.
- (-) A sweep is bounded and diagnostic; it is not a full multi-engine ingest
  of every detail page.
- (-) Interactive captcha sessions remain out of scope.
