---
title: "S5 — Интеграция, метрики и безопасное восстановление"
description: "Спецификация S5: требования и проверяемая приёмка."
updated: 2026-09-12
---
# S5 — Интеграция, метрики и безопасное восстановление

**Decisions**: [ADR 094](../adr/094-integration-recovery.md)
**Plan**: [Implementation plan](../plans/recovery-s5-integration-recovery.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Свести независимые изменения, выполнить end-to-end gates и подготовить управляемое историческое восстановление без автоматического deploy.

## Scope

- Свести patches из worker worktrees в integration worktree, сохраняя user changes; commits/push без отдельной команды не выполнять.
- Встроить agreed event IDs/counters P4 в runtime; distinguished observations/jobs/groups/messages, complete/partial sources и actual receipts.
- Независимая source inventory проверяет пропуски; выборка не обозначается как точный recall.
- Historical recovery только dry-run: подозрительные поля, duplicate links, ghost/uncertain deliveries, видимый Telegram backfill с provenance reconstructed.
- Нельзя автоматически сбрасывать ledger, повторно отправлять отсутствующие сообщения или подменять исторический текст.
- Подготовить rollback/canary/deploy/cleanup/replay runbooks; production mutations требуют отдельного подтверждения.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Targeted tests всех направлений, architecture/code/docs gates и non-network full suite.
2. Frozen recipe/regression gates в изолированном state; никакого reset production.
3. Shadow no-send walkthrough source→full text→fields→group→archive→fake receipt; cost/error/missing-field report.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: integration-only runtime wiring, application/tenant_runner.py, run_stats, dashboards, runbooks и final report. Содержательные изменения workers переносить с review.
Зависимости: S1–S4 и P1–P4 завершены и проверены.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

