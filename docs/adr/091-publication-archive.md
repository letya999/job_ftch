---
title: "091 — Неизменяемый архив публикаций"
description: "Решение S2 инициативы восстановления production."
updated: 2026-09-12
---
# 091 — Неизменяемый архив публикаций

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Сохранить полный точный текст публикации и достаточную цепочку исходных данных и результатов обработки независимо от оперативного TTL.

## Decision

Архив публикации — immutable snapshot с защищёнными evidence links; detailed operational logs остаются отдельным ограниченным классом данных.

- До отправки сохранить точный prepared text, entities/markup, ссылки и версию шаблона; после отправки связать Telegram receipt.
- Сохранить immutable snapshot canonical job, исходные observations/full source text, per-field evidence, terminal outcome, significant stage outcomes и версии graph/parser/model/prompt.
- Исторический архив не меняется при обновлении canonical job и удалении сообщения в Telegram.
- Необходимые observations и stage evidence защищены от TTL; активные intent/uncertain deliveries также защищены.
- Использовать существующие observation/outbox/store contracts; миграции additive, без production запуска.

## Consequences

Владелец: Последовательная задача. Зависимости: S1: receipt и actual delivery contract.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[S2 specification](../specs/recovery-s2-publication-archive.md).

