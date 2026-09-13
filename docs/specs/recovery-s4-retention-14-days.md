---
title: "S4 — 14-дневная диагностика и защищённый архив"
description: "Спецификация S4: требования и проверяемая приёмка."
updated: 2026-09-12
---
# S4 — 14-дневная диагностика и защищённый архив

**Decisions**: [ADR 093](../adr/093-retention-14-days.md)
**Plan**: [Implementation plan](../plans/recovery-s4-retention-14-days.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Ограничить подробную диагностику 14 днями, сохранив архив публикаций, активные задачи и необходимые operational identities.

## Scope

- OpenObserve operational/OpenAI streams и новые streams получают 14-day effective retention; проверить установленную версию, overrides и compactor.
- В Postgres detailed non-protected source/stage/ingest/funnel observations/outcomes имеют time-based 14-day policy, не только max-runs.
- Сохранить full source text каждой полученной вакансии в observation; опубликованные source evidence и stage results защищены бессрочно.
- Активный outbox, uncertain delivery, незавершённые задачи, актуальный tenant/source state и dedup identities не удаляются TTL логов.
- Другие tenant policies, включая unlimited raw retention, сохраняют совместимость: 14 дней применяются как согласованная ai_jobs diagnostic policy, не принудительный purge всех tenants.
- Очистка dry-run-first, indexed/batched/resumable; backup/restore proof и archive verification обязательны перед внешним удалением.
- Фактический deploy/переустановка retention/DELETE запрещены этой задачей без отдельного production разрешения.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Boundary 14 дней и защита references/active states/tenant unlimited retention.
2. Dry run не меняет данные; повторный пакет идемпотентен; сбой продолжим.
3. Archive lookup после удаления ephemeral diagnostics успешен; оценка объёмов дана раздельно по DB/OpenObserve/Docker.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: store cleanup/query implementation, tenant retention resolution, deploy/observability config, cleanup scripts/tests/runbook; snapshots baseline отдельно от logs.
Зависимости: S2 protection contract; S3; inventory P4.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

