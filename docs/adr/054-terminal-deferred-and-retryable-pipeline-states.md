---
title: "054 — Terminal, deferred and retryable pipeline states"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 054 — Terminal, deferred and retryable pipeline states

**Status**: ACCEPTED
**Date**: 2026-07-10
**Supersedes/Transitions**: ADR-010's recovery guidance remains historical.
`mark_processed` is transitional state and is valid only for a terminal
content observation, never for a retryable failure.

## Context

The original pipeline treated a source locator as permanently processed after
many outcomes. That conflated policy decisions, failed work, and delivery.
It also prevented content changed at the same locator from being reconsidered.

## Decision

Every raw observation has one of these lifecycle classes:

- **terminal**: an explicit drop, quarantine, or completed delivery;
- **deferred**: policy/provider/budget work is intentionally postponed;
- **retryable**: an unexpected stage or sink failure.

Only a terminal outcome may commit dedup claims and write processed state.
Deferred and retryable outcomes release their claims (or allow their TTL to
expire) and are eligible for a later attempt. Processed identity is content
versioned, so a changed payload at the same locator is not suppressed.
Durable delivery state is maintained separately by ADR-053.

## Consequences

- A transient failure cannot turn the next run into a duplicate.
- An unchanged replay can be skipped as an optimization, while a changed one
  is processed as a new observation.
- Future policy stages must explicitly classify degradation as terminal,
  deferred, or retryable rather than returning an unlabelled drop.
