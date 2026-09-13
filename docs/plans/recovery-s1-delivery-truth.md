---
title: "План S1 — Подтверждённая доставка"
description: "Последовательность реализации и handoff S1."
updated: 2026-09-12
---
# План S1 — Подтверждённая доставка

**Specification**: [S1](../specs/recovery-s1-delivery-truth.md)

## Dependencies and owner

Нет; старт одновременно с P1–P4.
Владелец: Последовательная задача. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Проследить все sender/publisher/outbox callers и закрепить текущую совместимость тестами.
2. Реализовать явный результат и durable intent/attempt/acknowledgement.
3. Проверить crash points и зафиксировать receipt contract для S2.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-s1-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

