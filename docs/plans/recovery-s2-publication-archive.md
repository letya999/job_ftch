---
title: "План S2 — Неизменяемый архив публикаций"
description: "Последовательность реализации и handoff S2."
updated: 2026-09-12
---
# План S2 — Неизменяемый архив публикаций

**Specification**: [S2](../specs/recovery-s2-publication-archive.md)

## Dependencies and owner

S1: receipt и actual delivery contract.
Владелец: Последовательная задача. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Согласовать reuse наблюдений и outbox; определить минимальные архивируемые outputs.
2. Добавить additive schema/store implementation и подготовку архива перед delivery.
3. Проверить чтение, tenant isolation и archive protection API для S4.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-s2-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

