---
title: "092 — Идентичность вакансий и дедупликация"
description: "Решение S3 инициативы восстановления production."
updated: 2026-09-12
---
# 092 — Идентичность вакансий и дедупликация

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Устранить известные дубли и stale grouping indexes без ошибочного объединения разных позиций.

## Decision

Использовать source-native identity прежде fuzzy match; canonical indexes обновляются при изменениях, ambiguous matches не становятся автоматическим merge.

- HH domain aliases сопоставлять по vacancy ID; URL normalization удаляет только известный tracking, сохраняя identity query parameters.
- Для cross-source match учитывать employer, title, substantial text, geography, compensation и native IDs; одинаковый title не достаточен.
- После enrichment/merge согласовывать canonical job, blocking keys и fingerprints атомарно; конфликты владения не перезаписывать молча.
- Обновление location/city/country/region происходит согласованно с нормализатором P2.
- Перед delivery повторно проверять stable identity по актуальным данным; операционное удаление Telegram не сбрасывает историю публикации.

## Consequences

Владелец: Последовательная задача. Зависимости: S2; финальная интеграция с результатами P1/P2 перед приёмкой.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[S3 specification](../specs/recovery-s3-stable-job-identity.md).

