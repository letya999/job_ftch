---
title: "P3 — Прокси, CAPTCHA и бюджет доступа"
description: "Спецификация P3: требования и проверяемая приёмка."
updated: 2026-09-12
---
# P3 — Прокси, CAPTCHA и бюджет доступа

**Decisions**: [ADR 097](../adr/097-proxy-captcha-budget.md)
**Plan**: [Implementation plan](../plans/recovery-p3-proxy-captcha-budget.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Устранить внутренние access-policy/budget/deadline причины отказов; проверить целевые маршруты без неподтверждённых утверждений о квоте.

## Scope

- Различить gateway health, target availability, provider quota и internal budget.
- Budget lifetime/reset явный; лимит проверяется при запросах, не только при adapter construction; byte estimate не равен provider billing.
- Проверить continuity proxy/cookies/user-agent/session и обработку 429/Retry-After.
- Разделить provider timeout и minimum remaining deadline; reserve времени согласован с общим source deadline.
- Challenge support/domain gates остаются явными; blanket bypass/auth disabling запрещены.
- Existing test CAPTCHA success не доказывает target acceptance; реальный challenge потребует отдельного bounded canary approval.
- Provider control-plane quota неизвестна без нужного API доступа; не читать/печать secrets, не делать новые платные solve/proxy probes в этом запуске.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. Budget boundary/reused adapter/reset/concurrency и deterministic quota-unknown tests.
2. Deadline insufficient vs unsupported vs policy denied vs provider error имеют разные reasons.
3. Mock challenge solution applied/accepted vs solved-not-accepted; session continuity/429 bounded retry.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: infrastructure/bypass/, proxy budget/accounting helpers и focused tests. Shared config.py/runtime YAML меняет только coordinator по переданному patch proposal.
Зависимости: Независимый старт; P1 согласует интерфейс доступа, S5 integration.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

