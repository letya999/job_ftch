---
title: "097 — Прокси, CAPTCHA и бюджет доступа"
description: "Решение P3 инициативы восстановления production."
updated: 2026-09-12
---
# 097 — Прокси, CAPTCHA и бюджет доступа

**Status**: PROPOSED
**Date**: 2026-09-12

## Context

Устранить внутренние access-policy/budget/deadline причины отказов; проверить целевые маршруты без неподтверждённых утверждений о квоте.

## Decision

Access capability, provider funds и internal gates независимы; bounded attempts получают честные причины завершения и проверяемые бюджеты.

- Различить gateway health, target availability, provider quota и internal budget.
- Budget lifetime/reset явный; лимит проверяется при запросах, не только при adapter construction; byte estimate не равен provider billing.
- Проверить continuity proxy/cookies/user-agent/session и обработку 429/Retry-After.
- Разделить provider timeout и minimum remaining deadline; reserve времени согласован с общим source deadline.
- Challenge support/domain gates остаются явными; blanket bypass/auth disabling запрещены.
- Existing test CAPTCHA success не доказывает target acceptance; реальный challenge потребует отдельного bounded canary approval.
- Provider control-plane quota неизвестна без нужного API доступа; не читать/печать secrets, не делать новые платные solve/proxy probes в этом запуске.

## Consequences

Владелец: Параллельная задача bypass. Зависимости: Независимый старт; P1 согласует интерфейс доступа, S5 integration.
Решение разрешает локальную реализацию и проверку; production deploy/миграции/удаление/массовые отправки требуют отдельного разрешения. Статус PROPOSED не означает production-ready реализацию.

Связанные существующие решения: [052](052-immutable-observation-ledger-and-content-versioned-replay.md), [053](053-durable-outbox-and-delivery-idempotency.md), [064](064-post-accept-enrichment-queue.md), [087](087-autonomous-tenant-search-lanes.md), [089](089-openai-observability-in-openobserve.md).

## Alternatives

Независимый второй pipeline/outbox и новые orchestration dependencies отклонены в этой инициативе: сначала переиспользуются текущие contracts и infrastructure.

## Specification

[P3 specification](../specs/recovery-p3-proxy-captcha-budget.md).

