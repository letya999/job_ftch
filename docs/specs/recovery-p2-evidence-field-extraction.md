---
title: "P2 — Доказательное извлечение компании, оплаты и географии"
description: "Спецификация P2: требования и проверяемая приёмка."
updated: 2026-09-12
---
# P2 — Доказательное извлечение компании, оплаты и географии

**Decisions**: [ADR 096](../adr/096-evidence-field-extraction.md)
**Plan**: [Implementation plan](../plans/recovery-p2-evidence-field-extraction.md)
**Initiative**: [Recovery execution](../plans/production-recovery-execution.md)

## Goal

Исправить field semantics и post-accept normalization, сохраняя неизвестность там, где данных нет.

## Scope

- Company extraction поддерживает вложенные name objects, отличает employer от recruiter/board/examples и хранит original/canonical names.
- Salary требует evidence и отличает pay от GMV, education/relocation/equipment budgets, market estimates, bonus/equity; period/gross/net согласованы.
- Разделить job location, candidate eligibility, employer location, relocation и work mode; язык/валюта не определяют страну.
- Location/city/region/country согласованы; conflict сохраняется явно; final render не выбирает stale geo.
- LLM prompt не домысливает и возвращает evidence; post-full-extraction validation использует общие нормализаторы.
- Enrichment completed не означает verified completeness; missing reasons not_disclosed/not_fetched/extraction_failed/conflict различимы.
- EvidenceDecisionNode остаётся terminal owner; post-accept не меняет ACCEPT/REVIEW decision. Model fields менять только additive при необходимости; сообщить coordinator.

## Runtime model

Tenant остаётся operational scope. Изменения backward-compatible и additive; SanitizeNode первый, type-changing paths через Stage, terminal decision принадлежит EvidenceDecisionNode. Полный source text относится к observations, секреты и профили не попадают в logs/fixtures/report.

## Metrics and observability

Результаты проверки связываются с run/source/observation/group/delivery и версией реализации. Ошибка, partial, missing, conflict, controlled drop и physical delivery различимы. Порогов live recall/precision без baseline не придумывать.

## Acceptance criteria

1. €1000 обучение перед €8000 зарплатой → salary €8000; $470 million GMV/€2000 relocation не salary.
2. Астана+Россия, Амстердам+RU conflicts; Russian/RUB abroad valid; remote eligibility отдельно.
3. Nested company, absent/hidden fields, full extraction retaining wrong prior values, final render normalization fixtures.

## Compatibility and safety

Работа только в своём worktree. Production reads разрешены лишь bounded/read-only; внешние writes, платные пробы, deploy, DB migrations/cleanup/replay, restart сервисов запрещены до отдельного подтверждения. Не коммитить/пушить без явной команды. Не читать raw secret files и не выводить DSN/пароли/ключи. Не запускать recipe reset на production.

## Technical appendix

Ownership: nodes/extraction.py, nodes/full_extraction.py, nodes/job_normalization.py, application/geo.py, publication geo rendering helpers, extraction prompts/LLM boundary и tests. Не редактировать group merge, parsers, sender/publisher или store migrations.
Зависимости: Независимый старт на fixtures; финальные tests на P1 payloads.
При необходимости изменить чужой ownership передать предложение integration owner, не редактировать чужие общие файлы параллельно.

## Open questions

Live provider quota, exact source aliases и требуемые production credentials остаются неизвестными до фактического получения. Исторические удалённые сообщения и прежние stage traces восстанавливаются только при наличии первичного архива. Если implementation требует нового shared contract, сначала согласовать с integration owner.

