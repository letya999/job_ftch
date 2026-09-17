---
title: "План P1 — Полный ingest и ключевые парсеры"
description: "Последовательность реализации и handoff P1."
updated: 2026-09-12
---
# План P1 — Полный ingest и ключевые парсеры

**Specification**: [P1](../specs/recovery-p1-complete-ingest-parsers.md)

## Dependencies and owner

Независимый старт; existing metadata compatibility. Сведение в S3/S5.
Владелец: Параллельная задача ingest. Модель: GPT-5.6 Luna, reasoning xhigh.

## Implementation sequence

1. Снять local characterization и записать field metadata contract для P2/S2.
2. Исправить common completeness/detail path и GetMatch/GeekJob/HireHi.
3. Проверить остальные named parsers, bounded public live probes без paid provider calls; передать patches, fixtures и coverage gaps S5.

## Verification

Сначала characterization и targeted tests; затем applicable gates из docs/operations/ci-cd.md. Shared runtime impact: architecture/code verification; docs: setup-docs/docs-verify; broad changes: non-network tests-all. Network/recipe evaluations только на изолированном state и с подтверждённым budget; external publishing отключён.

## Handoff

Создать docs/reports/recovery-p1-handoff.md с изменёнными файлами, точными test commands/results, blockers, dependencies, patch/review instructions и worktree path. Если gate не запускался или падал — указать явно. Не объявлять ready без runnable regression checks. S5 сверяет и переносит diffs, не требует commit workers.

## Rollback and rollout

Сохранить backward-compatible defaults; дать rollback patch/config plan и migration risks. Production rollout выполняется отдельным подтверждённым этапом после S5; до него только local/shadow/fake delivery и read-only audit.

