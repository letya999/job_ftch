---
title: "RerankerNode"
description: "Provider-neutral cross-encoder reranking по profile policy text."
updated: 2026-07-27
---
# RerankerNode

`RerankerNode` — provider-neutral reranking stage. В отличие от
`BgeRerankerNode`, он не знает конкретную model implementation и работает
через `CrossEncoderProvider`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `bge_reranker_max_score`,
`reranker_scores_by_profile`, `reranker_model`, `reranker_latency_ms`.

Если profiles/document/provider недоступны или provider возвращает неверное
число scores, узел возвращает record с `reranker_degradation`.

## Параметры

`provider: CrossEncoderProvider`.

`profiles: dict[profile_id, policy_text]`.

## Логика

Document = title + description. Для каждого profile узел вызывает
`provider.rerank(policy_text, [document])` и ожидает ровно один score. Scores
сохраняются per profile, max score — в `bge_reranker_max_score`.

## Границы

Reranker не меняет recall и не принимает terminal decision. Это feature для
downstream aggregator/evidence.
