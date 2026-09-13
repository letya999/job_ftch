---
title: "Production recovery — execution graph"
description: "Последовательные и параллельные задачи, ownership, gates и handoff."
updated: 2026-09-12
---
# Production recovery — execution graph

## Goal

Восстановить доказуемое получение, извлечение, объединение и доставку вакансий; диагностика 14 дней, архив публикаций и значимых этапов долговечный. [Audit context](../reports/production-recovery-audit-context.md).

## Execution graph

```text
START ─┬─ S1 delivery → S2 archive → S3 identity → S4 retention → S5 integration
       ├─ P1 complete ingest/parsers ────────────┬──────────────→ S5
       ├─ P2 evidence field extraction ─────────┘ (S3 final gate)
       ├─ P3 proxy/CAPTCHA/budgets ────────────────────────────→ S5
       └─ P4 source/storage/coverage baseline ──→ S4 inventory → S5 metrics
```

S3 можно начинать на fixtures, но его приёмка требует P1/P2; S4 требует ранний P4 inventory. S5 ждёт все outputs. Production deploy/cleanup/replay не часть автоматического исполнения.

## Work packages

| ID | Step | ADR | Specification | Plan |
|---|---|---|---|---|
| S1 | Подтверждённая доставка | [090](../adr/090-delivery-truth.md) | [Spec](../specs/recovery-s1-delivery-truth.md) | [Plan](recovery-s1-delivery-truth.md) |
| S2 | Неизменяемый архив публикаций | [091](../adr/091-publication-archive.md) | [Spec](../specs/recovery-s2-publication-archive.md) | [Plan](recovery-s2-publication-archive.md) |
| S3 | Идентичность вакансий и дедупликация | [092](../adr/092-stable-job-identity.md) | [Spec](../specs/recovery-s3-stable-job-identity.md) | [Plan](recovery-s3-stable-job-identity.md) |
| S4 | 14-дневная диагностика и защищённый архив | [093](../adr/093-retention-14-days.md) | [Spec](../specs/recovery-s4-retention-14-days.md) | [Plan](recovery-s4-retention-14-days.md) |
| S5 | Интеграция, метрики и безопасное восстановление | [094](../adr/094-integration-recovery.md) | [Spec](../specs/recovery-s5-integration-recovery.md) | [Plan](recovery-s5-integration-recovery.md) |
| P1 | Полный ingest и ключевые парсеры | [095](../adr/095-complete-ingest-parsers.md) | [Spec](../specs/recovery-p1-complete-ingest-parsers.md) | [Plan](recovery-p1-complete-ingest-parsers.md) |
| P2 | Доказательное извлечение компании, оплаты и географии | [096](../adr/096-evidence-field-extraction.md) | [Spec](../specs/recovery-p2-evidence-field-extraction.md) | [Plan](recovery-p2-evidence-field-extraction.md) |
| P3 | Прокси, CAPTCHA и бюджет доступа | [097](../adr/097-proxy-captcha-budget.md) | [Spec](../specs/recovery-p3-proxy-captcha-budget.md) | [Plan](recovery-p3-proxy-captcha-budget.md) |
| P4 | Контроль источников, пропусков и роста хранения | [098](../adr/098-source-coverage-observability.md) | [Spec](../specs/recovery-p4-source-coverage-observability.md) | [Plan](recovery-p4-source-coverage-observability.md) |

## Isolation and integration

Пять новых user-owned задач, каждая GPT-5.6 Luna xhigh в отдельном Git worktree. Одна задача владеет S1–S5 и финальным integration; остальные P1–P4. Shared runtime/config/schema и generated docs сводит S5. Workers редактируют только свои файлы; ownership расширяется через coordination. Не устанавливать новый orchestrator, не делать commits/push без явной команды.

## Baseline and contracts

Зафиксировать characterization прежде изменений. Existing metadata/protocols использовать по умолчанию. P1 рано передаёт payload/evidence contract P2 и S2; S1 даёт receipt S2; S2 даёт archive protection S4; P2 даёт geo contract S3; P4 даёт event/counter definitions S5. До согласования shared contracts изменения ограничены совместимыми helpers/fixtures.

## Gates

1. Каждая работа имеет runnable regressions и sanitized handoff report.
2. S5 интегрирует через review/apply_patch; без reset/stash/delete пользовательских изменений.
3. Targeted tests, architecture/code/docs, non-network suite и isolated recipe gates.
4. Итоговый shadow no-send report и dry-run historical recovery.
5. Отдельное подтверждение production rollout; отдельно backup/restore и cleanup разрешение.

## Startup coordination

Реестр: [Task dispatch](../reports/production-recovery-task-dispatch.md). Если follow-up с реальными IDs ещё не пришёл, integration owner находит новые Recovery задачи через list_threads по проекту и точным заголовкам; clientThreadId не используется в read/send/wait. После readiness отправляет workers свой thread ID и ownership/contracts. Нельзя создавать дубли задач из-за длительной подготовки worktrees. Начальная независимая работа S1/P1–P4 не зависит от readiness остальных.

## Pending operational decisions

Проверка платного реального CAPTCHA challenge, получение control-plane API квоты, изменение существующих Telegram сообщений и production cleanup требуют отдельного разрешённого этапа. Нельзя обещать восстановление отсутствующего исторического содержимого.
