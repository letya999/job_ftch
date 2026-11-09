---
title: "110 — Проверка публикаций по недавней истории"
description: "Повторные объявления из разных источников не обходят журнал публикаций при смене URL и ID."
updated: 2026-11-10
---
# 110 — Проверка публикаций по недавней истории

**Status**: ACCEPTED  
**Date**: 2026-11-10

## Context

The publication ledger prevents repeated group IDs and canonical URLs, but two
sources can publish the same vacancy under different IDs and URLs in separate
scheduler attempts. Batch-local `same_vacancy` cannot see the earlier delivery.
Confirmed Telegram messages 4261/4246 are one example. HH.ru and HH.kz also
expose the same numeric vacancy ID under different hosts (4225/4177).

## Decision

Normalize known HH vacancy URLs to their numeric vacancy identity. Keep a
bounded, target-specific history of confirmed publication cards and apply the
existing conservative `same_vacancy` comparison before another send. Record a
card only after Telegram confirms delivery. The history supplements the durable
ID, URL and outbox ledgers; uncertain sends stay held for reconciliation.

Cross-source matching still requires substantial content and compatible
employer, geography and compensation. Title alone never suppresses a vacancy.
Different native IDs from the same source remain separate unless the existing
ID or URL ledger proves they are the same posting.
The history holds the latest 250 confirmed cards so storage and comparison work
stay bounded. Older source-specific IDs and URLs remain covered by their
existing ledgers.

## Consequences

Near-identical reposts across scheduler attempts are skipped. A short summary
and a substantially rewritten full posting may remain distinct; automatic
suppression in that case risks hiding separate openings. Existing delivered
cards can be seeded from confirmed outbox receipts during deployment.
