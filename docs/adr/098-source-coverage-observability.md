---
title: "098 — Контроль источников, пропусков и роста хранения"
description: "Решение P4 инициативы восстановления production."
updated: 2026-09-12
---
# 098 — Контроль источников, пропусков и роста хранения

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Собрать воспроизводимую baseline matrix всех sources, независимую выборку пропусков и размеры хранилищ; подготовить metrics contract.

## Decision

Аудит использует независимую внешнюю выборку и фактическое состояние хранилищ; source capability/health не подменяет field correctness или delivery.

- Переснять актуальный source registry; отдельные enabled/degraded/reliable/detail-complete/field-correct/profile-contributing признаки.
- Canonicalize aliases/keyword expansions; repeated observations не считать unique jobs.
- Исторический интервал ограничить реально доступной историей; установить last complete/nonempty success и причины regression.
- Read-only SSH/Postgres/OpenObserve inventory с statement timeout и default_transaction_read_only; никакого production ingest/run/replay.
- Измерить DB tables/indexes/TOAST/WAL, OpenObserve volume и Docker logs раздельно; не предполагать причину роста.
- Сопоставить bounded independent board inventory и observations; пропуски классифицировать по этапу, не утверждать точный recall при sample.
- Передать unique event IDs, counter definitions, conservation checks и missing-available denominator; runtime instrumentation делает S5.

## Consequences

Владелец: Параллельная задача audit. Зависимости: Независимый старт; inventory нужен S4, metrics contract нужен S5.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[P4 specification](../specs/recovery-p4-source-coverage-observability.md).

