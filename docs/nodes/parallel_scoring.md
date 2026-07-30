---
title: "ParallelScoringNode"
description: "BGE-M3 parallel scoring: dense/sparse positive-negative margins."
updated: 2026-07-27
---
# ParallelScoringNode

`ParallelScoringNode` считает multi-branch relevance score из BGE-M3 dense и
sparse vectors, уже записанных в `JobRecord.metadata`.

## Вход и выход

**Вход:** `JobRecord` с `metadata.bgem3_dense`; optional `bgem3_sparse`.

**Выход:** `JobRecord` с branch metrics и `parallel_final_score`.

Если dense vector отсутствует, узел no-op.

## Branches

Dense positive anchors: max cosine/dot similarity к positive shot anchors.

Sparse positive anchors: normalized sparse dot к positive sparse anchors.

Role anchors: dense similarity к target role anchors, сохраняется как
observability/tiebreaker.

Dense/sparse negative anchors: gates для contrastive margin.

## Fusion

Final score строится из contrastive dense и sparse margins:
positive score минус negative similarity. Role branch не добавляется в margin,
чтобы не создавать постоянный pedestal для любого tech post.

Margin проходит через sigmoid с `margin_k`. В metadata пишутся
`parallel_score_dense`, `parallel_score_sparse`, `parallel_score_role`,
`parallel_neg_sim`, `parallel_dense_margin`, `parallel_sparse_margin`,
`parallel_final_score`.

## Границы

Узел не принимает terminal decision. `RoutingNode`, `DecisionAggregatorNode` или
evidence policy могут использовать score как один из сигналов.
