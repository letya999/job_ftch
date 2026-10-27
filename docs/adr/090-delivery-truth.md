---
title: "090 — Подтверждённая доставка"
description: "Решение S1 инициативы восстановления production."
updated: 2026-09-12
---
# 090 — Подтверждённая доставка

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Исправить ложный успех отправки, сохранить receipt и различать отказ рендера, retryable failure и неопределённую доставку.

## Decision

Результат физической доставки хранится отдельно от terminal policy; использовать существующий outbox и явное подтверждение transport.

- Счётчик sent и delivered ledger обновляются только после подтверждённой отправки.
- Результат отправки содержит состояние, target, message_id и время подтверждения, когда они доступны; сохранить совместимость остальных sender consumers через явную адаптацию.
- Отказ валидации имеет reason code и не считается доставкой.
- После неизвестного результата внешней отправки запись не повторяется автоматически; предусмотреть сверку.
- Связать реальный Telegram publisher с существующим durable outbox; собственный ключ не считать гарантией exactly-once Telegram.

## Consequences

Владелец: Последовательная задача. Зависимости: Нет; старт одновременно с P1–P4.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[S1 specification](../specs/recovery-s1-delivery-truth.md).

