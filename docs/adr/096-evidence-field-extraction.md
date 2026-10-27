---
title: "096 — Доказательное извлечение компании, оплаты и географии"
description: "Решение P2 инициативы восстановления production."
updated: 2026-09-12
---
# 096 — Доказательное извлечение компании, оплаты и географии

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Исправить field semantics и post-accept normalization, сохраняя неизвестность там, где данных нет.

## Decision

Каждое извлечённое значение основано на source evidence и проходит семантическую проверку; missing/conflict не заполняется догадкой.

- Company extraction поддерживает вложенные name objects, отличает employer от recruiter/board/examples и хранит original/canonical names.
- Salary требует evidence и отличает pay от GMV, education/relocation/equipment budgets, market estimates, bonus/equity; period/gross/net согласованы.
- Разделить job location, candidate eligibility, employer location, relocation и work mode; язык/валюта не определяют страну.
- Location/city/region/country согласованы; conflict сохраняется явно; final render не выбирает stale geo.
- LLM prompt не домысливает и возвращает evidence; post-full-extraction validation использует общие нормализаторы.
- Enrichment completed не означает verified completeness; missing reasons not_disclosed/not_fetched/extraction_failed/conflict различимы.
- EvidenceDecisionNode остаётся terminal owner; post-accept не меняет ACCEPT/REVIEW decision. Model fields менять только additive при необходимости; сообщить coordinator.

## Consequences

Владелец: Параллельная задача extraction. Зависимости: Независимый старт на fixtures; финальные tests на P1 payloads.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[P2 specification](../specs/recovery-p2-evidence-field-extraction.md).

