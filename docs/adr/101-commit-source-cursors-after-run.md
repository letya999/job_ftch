---
title: "101 — Commit Telegram cursors after a successful run"
description: "Defer tenant Telegram cursors until pipeline processing finishes so interrupted runs replay unread messages."
updated: 2026-10-18
---
# 101 — Commit Telegram cursors after a successful run

**Status**: ACCEPTED
**Date**: 2026-10-18

## Context

Telegram sources advanced their persisted cursor as soon as fetching finished.
The pipeline can then be interrupted before processing and delivery finish. On
restart, the next run begins after that cursor and can miss fetched messages.

## Decision

Each tenant run stages its Telegram cursor writes while its pipeline runs and commits them
only when that run returns with no failed items. An exception, cancellation or
failed item discards the staged positions. Existing deduplication and delivery idempotency handle
replayed messages. Direct cursor use outside a tenant run keeps immediate writes.

The tenant store owns one cursor object so source tasks and the runner share the
same staged state. Separate async task contexts and tenants remain isolated.

## Consequences

Interrupted runs repeat source reads. This spends some fetch time but avoids
skipping observations. Cursor commits are not a cross-key database transaction;
an interruption during commit may replay only some sources, which deduplication
must tolerate.
