---
title: "S2 — Неизменяемый архив публикаций"
description: "Спецификация S2: требования и проверяемая приёмка."
updated: 2026-09-12
---
# S2 — Неизменяемый архив публикаций

**Decisions**: [ADR 091](../adr/091-publication-archive.md)
**Plan**: [Implementation plan](../plans/recovery-s2-publication-archive.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Сохранить полный точный текст публикации и достаточную цепочку исходных данных и результатов обработки независимо от оперативного TTL.

## Scope

- До отправки сохранить точный prepared text, entities/markup, ссылки и версию шаблона; после отправки связать Telegram receipt.
- Сохранить immutable snapshot canonical job, исходные observations/full source text, per-field evidence, terminal outcome, significant stage outcomes и версии graph/parser/model/prompt.
- Исторический архив не меняется при обновлении canonical job и удалении сообщения в Telegram.
- Необходимые observations и stage evidence защищены от TTL; активные intent/uncertain deliveries также защищены.
- Использовать существующие observation/outbox/store contracts; миграции additive, без production запуска.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Изменение canonical job не меняет archived text и archived поля.
2. Публикация старше 14 дней остаётся воспроизводимой после модельной очистки.
3. Receipt, source evidence и stage outcomes доступны в tenant-scoped query; cross-tenant access исключён.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: domain/application persistence contracts, infrastructure/stores migrations/backends; archive queries/tests. Номера миграций выбирать после проверки реестра.
Зависимости: S1: receipt и actual delivery contract.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

