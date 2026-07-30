---
title: "UncertaintyRouterNode"
description: "Cheap three-zone annotation для LLM call policy."
updated: 2026-07-27
---
# UncertaintyRouterNode

`UncertaintyRouterNode` помечает record как consistent positive, consistent
negative или disagreement перед дорогим LLM relevance judge.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с metadata `uncertainty_zone`, `needs_llm_review`,
`uncertainty_recommendation`.

Узел никогда не пишет terminal `routing_decision`.

## Параметры

`low_threshold = 0.20`, `high_threshold = 0.50`.

Score берётся из `metadata.parallel_final_score`, fallback —
`item.relevance_score`.

## Логика

Low score + low/unknown quality => `consistent_negative`, recommendation
`reject`.

High score + acceptable/unknown quality => `consistent_positive`,
recommendation `accept`.

Иначе `disagreement`, `needs_llm_review = True`.

High/critical risk принудительно делает item negative.

## Границы

Узел управляет расходом LLM и помечает зону неопределённости. Финальное решение
остаётся у relevance/decision stages.
