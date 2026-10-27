---
title: "План S4 — 14-дневная диагностика и защищённый архив"
description: "Последовательность реализации и handoff S4."
updated: 2026-09-12
---
# План S4 — 14-дневная диагностика и защищённый архив

**Specification**: [S4](../specs/recovery-s4-retention-14-days.md)

## Dependencies and owner

S2 protection contract; S3; inventory P4.
Владелец: Последовательная задача. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. На основе P4 inventory определить таблицы/KV prefixes и защищённые связи.
2. Реализовать retention и dry-run cleanup локально на тестовой БД.
3. Подготовить version-checked OpenObserve settings и backup/restore/rollout runbook; не применять на сервере.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-s4-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

