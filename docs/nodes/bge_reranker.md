---
title: "Graph node: bge_reranker"
description: "Graph id для optional BgeRerankerNode."
updated: 2026-07-27
---
# Graph node: `bge_reranker`

`bge_reranker` — graph id для optional `BgeRerankerNode`.

Контракт: `JobRecord -> JobRecord`. Успешный run пишет
`metadata.bge_reranker_max_score`; degraded run пишет
`metadata.reranker_degradation`.

Default pipeline использует `ParallelScoringNode`, поэтому reranker надо
подключать явно в graph/recipe variant.

См. [BgeRerankerNode](bge_reranker_node.md).
