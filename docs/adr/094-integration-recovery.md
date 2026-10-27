---
title: "094 — Интеграция, метрики и безопасное восстановление"
description: "Решение S5 инициативы восстановления production."
updated: 2026-09-12
---
# 094 — Интеграция, метрики и безопасное восстановление

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Свести независимые изменения, выполнить end-to-end gates и подготовить управляемое историческое восстановление без автоматического deploy.

## Decision

Единственный integration owner проводит dependency gates и production handoff; независимые workers не делают общий deploy или data repair.

- Свести patches из worker worktrees в integration worktree, сохраняя user changes; commits/push без отдельной команды не выполнять.
- Встроить agreed event IDs/counters P4 в runtime; distinguished observations/jobs/groups/messages, complete/partial sources и actual receipts.
- Независимая source inventory проверяет пропуски; выборка не обозначается как точный recall.
- Historical recovery только dry-run: подозрительные поля, duplicate links, ghost/uncertain deliveries, видимый Telegram backfill с provenance reconstructed.
- Нельзя автоматически сбрасывать ledger, повторно отправлять отсутствующие сообщения или подменять исторический текст.
- Подготовить rollback/canary/deploy/cleanup/replay runbooks; production mutations требуют отдельного подтверждения.

## Consequences

Владелец: Последовательная задача. Зависимости: S1–S4 и P1–P4 завершены и проверены.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[S5 specification](../specs/recovery-s5-integration-recovery.md).

