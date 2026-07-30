---
title: "AcceptTemplatePresentationNode"
description: "Zero-cost deterministic presentation только для ACCEPT records."
updated: 2026-07-27
---
# AcceptTemplatePresentationNode

`AcceptTemplatePresentationNode` создаёт deterministic `PresentableJob` без LLM
для вакансий, которые уже получили routing decision `ACCEPT`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `presentable`, если record accepted и `presentable`
ещё не заполнен.

Для `REVIEW`, `REJECT` и records с уже готовым `presentable` узел no-op.

## Логика

Узел вызывает shared fallback formatter `_template_present()` из
`presentable_text.py`. Это тот же deterministic template, который
`PresentableTextNode` использует при budget exhaustion или LLM failure.

## Границы

Узел не вызывает LLM, не кеширует результат и не меняет routing decision. Это
дешёвый post-routing presentation stage.
