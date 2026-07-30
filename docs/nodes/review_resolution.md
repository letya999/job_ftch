---
title: "ReviewResolutionNode"
description: "Bounded second-opinion LLM только для primary REVIEW."
updated: 2026-07-27
---
# ReviewResolutionNode

`ReviewResolutionNode` тратит более сильный/дополнительный LLM call только на
records, где primary LLM явно сказал `review` и текущий routing decision тоже
`REVIEW`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с возможно изменённым `routing_decision` и metadata
`review_resolution`.

Если record не REVIEW, primary `_llm_relevance.decision` не `review` или
`max_calls` исчерпан, узел no-op.

## Параметры

`llm`, `decision_brief`, `accept_confidence = 0.70`,
`max_calls = 40`, `timeout_seconds = 25.0`.

## Логика

Prompt включает profile brief, основные поля вакансии, primary reasoning и
обрезанное description. LLM возвращает `accept/reject/review`, confidence и
reasoning.

`accept` применяется только если confidence >= `accept_confidence`.
`reject` применяется сразу. Остальное остаётся `REVIEW`.

Failure не падает pipeline: в metadata пишется trace outcome `failed`.

## Границы

Это bounded escalation strategy для спорных cases, а не основной relevance
judge. Узел не должен запускаться на каждом item.
