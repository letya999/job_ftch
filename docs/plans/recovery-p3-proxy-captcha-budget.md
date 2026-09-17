---
title: "План P3 — Прокси, CAPTCHA и бюджет доступа"
description: "Последовательность реализации и handoff P3."
updated: 2026-09-12
---
# План P3 — Прокси, CAPTCHA и бюджет доступа

**Specification**: [P3](../specs/recovery-p3-proxy-captcha-budget.md)

## Dependencies and owner

Независимый старт; P1 согласует интерфейс доступа, S5 integration.
Владелец: Параллельная задача bypass. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Проследить budget singleton и все request/adaptation callers.
2. Исправить request-level accounting/reset/deadline semantics и reason codes.
3. Подготовить sanitized health/quota probe и bounded real-site canary runbook; передать default-setting предложения S5.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-p3-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

