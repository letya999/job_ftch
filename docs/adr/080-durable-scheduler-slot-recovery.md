---
title: "080 - Durable Scheduler Slot Recovery"
description: "Планировщик Telegram восстанавливает пропущенные ingest-слоты и незавершённую публикацию после сбоя или перезапуска."
updated: 2026-08-02
---
# 080 - Durable Scheduler Slot Recovery

**Status**: ACCEPTED
**Date**: 2026-08-02

## Context

The Telegram scheduler persisted only the last attempt and a publish retry
window. A laptop shutdown could therefore hide a missed interval, and a
network error from Telegram could be logged per card while the scheduler
cleared the delivery intent. The next run then had no durable record that work
was still owed.

## Decision

- Persist a bounded `bot_scheduler:journal` of fixed schedule slots and a
  `bot_scheduler:next_due_at` cursor in the existing run-state store.
- Treat a slot as complete only when ingest succeeded and publication either
  delivered every eligible card or skipped cards already present in the
  idempotency ledger.
- Reconcile incomplete slots before creating a later ingest slot. A successful
  ingest with incomplete publication is recovered through the existing pending
  publish window without rerunning ingest unnecessarily.
- Classify transport failures as retryable delivery failures, retain the
  pending window, and persist the failure for inspection.

## Consequences

- (+) Restart and laptop downtime no longer silently discard a scheduled slot.
- (+) A Telegram outage is retried, while the publish ledger prevents duplicate
  cards after partial success.
- (+) Operators can distinguish incomplete ingest from incomplete publication.
- (-) Run state contains a small bounded journal and requires cleanup policy
  for permanently unusable targets or malformed cards.
