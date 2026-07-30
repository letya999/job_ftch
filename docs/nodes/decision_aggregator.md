---
title: "DecisionAggregatorNode"
description: "Legacy/alternate deterministic aggregator of LLM, profile, BGE and lexical signals."
updated: 2026-07-27
---
# DecisionAggregatorNode

`DecisionAggregatorNode` — deterministic terminal aggregator для legacy/alternate
pipeline variants, где LLM relevance, profile score, BGE margin и lexical
conflicts уже лежат в `JobRecord.metadata`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `routing_decision`, review reason
`decision_aggregator:<reason>` и metadata `decision_aggregator_trace`.

## Параметры

`accept_profile_score`, `review_profile_score`, `accept_llm_confidence`,
`allow_missing_llm_rescue`, `allow_reject_rescue`,
`require_no_profile_conflict`.

## Логика

Hard constraint contradiction всегда даёт `REJECT`.

LLM accept с достаточной confidence даёт `ACCEPT`, если нет требуемого profile
conflict veto.

Missing/reject LLM может быть rescued независимым profile/BGE сигналом, если
соответствующие flags разрешены.

Слабый independent signal при LLM reject даёт `REJECT`; смешанные или неполные
сигналы дают `REVIEW`.

## Границы

В текущей evidence architecture основная policy boundary — `DecisionNode`.
`DecisionAggregatorNode` нужен для compatibility/experimental graph variants,
а не как второй источник истины рядом с `EvidenceDecisionNode`.
