---
title: "RoutingNode"
description: "Historical terminal routing writer для historical_best/legacy presets."
updated: 2026-07-27
---
# RoutingNode

`RoutingNode` — historical terminal writer, сохранённый для versioned baseline
preset `historical_best` и legacy graph variants.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `routing_decision` и review reasons.

## Логика legacy mode

Если metadata `_llm_relevance.decision` присутствует, он выбирает ACCEPT для
`accept`, иначе REJECT, и добавляет reason `llm_relevance:<decision>`.

Если `uncertainty_recommendation` равен `accept` или `reject`, использует его.

Иначе смотрит `parallel_final_score`: выше accept threshold — ACCEPT, выше
review threshold в текущем коде всё равно REJECT с reason `profile_review`,
ниже — REJECT.

Quality override может перевести ACCEPT в REJECT при низком `quality_score`.

## Declarative policy mode

Если через `configure_graph_policy()` передан policy mode `weighted` или
`claims`, узел делегирует evaluation в `application.graph.policy.DecisionPolicy`
и пишет `decision_policy_trace`.

## Границы

Это legacy/historical node. В текущей evidence architecture основной terminal
policy owner — `DecisionNode` через `EvidenceDecisionNode`.
