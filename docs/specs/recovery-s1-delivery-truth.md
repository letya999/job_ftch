---
title: "S1 — Подтверждённая доставка"
description: "Спецификация S1: требования и проверяемая приёмка."
updated: 2026-09-12
---
# S1 — Подтверждённая доставка

**Decisions**: [ADR 090](../adr/090-delivery-truth.md)
**Plan**: [Implementation plan](../plans/recovery-s1-delivery-truth.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Исправить ложный успех отправки, сохранить receipt и различать отказ рендера, retryable failure и неопределённую доставку.

## Scope

- Счётчик sent и delivered ledger обновляются только после подтверждённой отправки.
- Результат отправки содержит состояние, target, message_id и время подтверждения, когда они доступны; сохранить совместимость остальных sender consumers через явную адаптацию.
- Отказ валидации имеет reason code и не считается доставкой.
- После неизвестного результата внешней отправки запись не повторяется автоматически; предусмотреть сверку.
- Связать реальный Telegram publisher с существующим durable outbox; собственный ключ не считать гарантией exactly-once Telegram.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Fake bot: render rejected → 0 вызовов API, sent=0, нет delivered отметки.
2. Успешный ответ → receipt сохранён; ошибка и неопределённый ответ не становятся DELIVERED.
3. Crash-point tests до отправки и после отправки до acknowledgement; tenant/target isolation.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: application/channel_publisher.py, application/outbox.py, adapters/telegram_bot/sender.py и все обнаруженные callers/ports; соответствующие tests.
Зависимости: Нет; старт одновременно с P1–P4.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

