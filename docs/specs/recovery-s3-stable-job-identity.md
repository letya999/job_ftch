---
title: "S3 — Идентичность вакансий и дедупликация"
description: "Спецификация S3: требования и проверяемая приёмка."
updated: 2026-09-12
---
# S3 — Идентичность вакансий и дедупликация

**Decisions**: [ADR 092](../adr/092-stable-job-identity.md)
**Plan**: [Implementation plan](../plans/recovery-s3-stable-job-identity.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Устранить известные дубли и stale grouping indexes без ошибочного объединения разных позиций.

## Scope

- HH domain aliases сопоставлять по vacancy ID; URL normalization удаляет только известный tracking, сохраняя identity query parameters.
- Для cross-source match учитывать employer, title, substantial text, geography, compensation и native IDs; одинаковый title не достаточен.
- После enrichment/merge согласовывать canonical job, blocking keys и fingerprints атомарно; конфликты владения не перезаписывать молча.
- Обновление location/city/country/region происходит согласованно с нормализатором P2.
- Перед delivery повторно проверять stable identity по актуальным данным; операционное удаление Telegram не сбрасывает историю публикации.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. HH vacancy 137177581 через hh.ru и региональный hh.kz → одна публикационная идентичность.
2. OpenBrain HireSeeker/HH positive fixture; разные requisitions/locations и Toloka jid → negative fixtures.
3. Enrichment меняет company/geo → индексы согласованы; fingerprint conflict сохраняется/разрешается явно.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: domain/job_group.py, nodes/aggregation.py, infrastructure/backends/jobs/, identity helpers и tests. P2 не правит merge/group files.
Зависимости: S2; финальная интеграция с результатами P1/P2 перед приёмкой.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

