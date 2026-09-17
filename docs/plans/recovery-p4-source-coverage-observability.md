---
title: "План P4 — Контроль источников, пропусков и роста хранения"
description: "Последовательность реализации и handoff P4."
updated: 2026-09-12
---
# План P4 — Контроль источников, пропусков и роста хранения

**Specification**: [P4](../specs/recovery-p4-source-coverage-observability.md)

## Dependencies and owner

Независимый старт; inventory нужен S4, metrics contract нужен S5.
Владелец: Параллельная задача audit. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Инвентаризировать registry/history и storage read-only; передать ранний report S4.
2. Собрать bounded detail/coverage samples ключевых boards; неопределённые aliases выделить.
3. Определить event/counter contract и regression dataset; передать sanitized outputs S5.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-p4-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

