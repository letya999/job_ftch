---
title: "095 — Полный ingest и ключевые парсеры"
description: "Решение P1 инициативы восстановления production."
updated: 2026-09-12
---
# 095 — Полный ingest и ключевые парсеры

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Устранить потерю detail content и полей в common ingest и известных job boards.

## Decision

Discovery payload и complete vacancy различаются по содержимому; detail extraction запускается для неполных records, source evidence сохраняется до interpretation.

- rich_payload достаточен только при действительном full content; title/ID/snippet требуют detail.
- Переиспользовать shared API/JSON-LD/DOM extraction с field provenance; не добавлять host switches в core.
- GeekJob получает detail; GetMatch не пропускает detail из-за API-sniffer fallback; HireHi сохраняет organisation/location/workmode/baseSalary evidence без доверия заглушкам.
- Проверить Hirify API/429, Habr detail/deadlines, HH parser и discovery/pagination всех ключевых boards; bypass implementation принадлежит P3.
- Не выдавать CSS/JS/Next Flight/browser check/общую доску за vacancy text.
- Partial detail/source outcomes явны и retryable; проверить existing snapshot protection и content-version replay.
- Названия HRFi/HRK/GitJob/Jobseeker/Gcic сопоставить с точными source URLs; непонятные aliases включить в unresolved matrix.

## Consequences

Владелец: Параллельная задача ingest. Зависимости: Независимый старт; existing metadata compatibility. Сведение в S3/S5.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[P1 specification](../specs/recovery-p1-complete-ingest-parsers.md).

