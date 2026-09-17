---
title: "План P2 — Доказательное извлечение компании, оплаты и географии"
description: "Последовательность реализации и handoff P2."
updated: 2026-09-12
---
# План P2 — Доказательное извлечение компании, оплаты и географии

**Specification**: [P2](../specs/recovery-p2-evidence-field-extraction.md)

## Dependencies and owner

Независимый старт на fixtures; финальные tests на P1 payloads.
Владелец: Параллельная задача extraction. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Зафиксировать ошибочные salary/geo/company cases и проследить все callers нормализации.
2. Исправить детерминированные parsers, full extraction и prompt contract.
3. Согласовать P1 metadata и S3 geo merge; передать patches/evidence/missing reasons и тестовые результаты.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-p2-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

