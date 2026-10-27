---
title: "План S3 — Идентичность вакансий и дедупликация"
description: "Последовательность реализации и handoff S3."
updated: 2026-09-12
---
# План S3 — Идентичность вакансий и дедупликация

**Specification**: [S3](../specs/recovery-s3-stable-job-identity.md)

## Dependencies and owner

S2; финальная интеграция с результатами P1/P2 перед приёмкой.
Владелец: Последовательная задача. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Закрепить точные positive/negative fixtures.
2. Исправить native identity и согласованность индексов/merge.
3. Интегрировать P1/P2, выполнить pre-publication duplicate gate и dry-run historical consistency report.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-s3-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

