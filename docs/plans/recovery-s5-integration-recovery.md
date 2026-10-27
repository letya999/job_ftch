---
title: "План S5 — Интеграция, метрики и безопасное восстановление"
description: "Последовательность реализации и handoff S5."
updated: 2026-09-12
---
# План S5 — Интеграция, метрики и безопасное восстановление

**Specification**: [S5](../specs/recovery-s5-integration-recovery.md)

## Dependencies and owner

S1–S4 и P1–P4 завершены и проверены.
Владелец: Последовательная задача. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Дождаться workers по task IDs, проверить diffs/tests; получить отсутствующие outputs follow-up запросами.
2. Интегрировать patches через apply_patch без commits и разрешить shared-file conflicts.
3. Выполнить end-to-end verification, dry-run recovery и итоговый отчёт; остановиться перед production mutations.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-s5-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

