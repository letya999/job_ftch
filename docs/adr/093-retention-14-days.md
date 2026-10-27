---
title: "093 — 14-дневная диагностика и защищённый архив"
description: "Решение S4 инициативы восстановления production."
updated: 2026-09-12
---
# 093 — 14-дневная диагностика и защищённый архив

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Ограничить подробную диагностику 14 днями, сохранив архив публикаций, активные задачи и необходимые operational identities.

## Decision

Два класса хранения: ephemeral diagnostics 14 дней и долговечный publication evidence; cleanup никогда не выводит необходимость данных только из их возраста.

- OpenObserve operational/OpenAI streams и новые streams получают 14-day effective retention; проверить установленную версию, overrides и compactor.
- В Postgres detailed non-protected source/stage/ingest/funnel observations/outcomes имеют time-based 14-day policy, не только max-runs.
- Сохранить full source text каждой полученной вакансии в observation; опубликованные source evidence и stage results защищены бессрочно.
- Активный outbox, uncertain delivery, незавершённые задачи, актуальный tenant/source state и dedup identities не удаляются TTL логов.
- Другие tenant policies, включая unlimited raw retention, сохраняют совместимость: 14 дней применяются как согласованная ai_jobs diagnostic policy, не принудительный purge всех tenants.
- Очистка dry-run-first, indexed/batched/resumable; backup/restore proof и archive verification обязательны перед внешним удалением.
- Фактический deploy/переустановка retention/DELETE запрещены этой задачей без отдельного production разрешения.

## Consequences

Владелец: Последовательная задача. Зависимости: S2 protection contract; S3; inventory P4.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[S4 specification](../specs/recovery-s4-retention-14-days.md).

