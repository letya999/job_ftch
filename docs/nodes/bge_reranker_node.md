---
title: "BgeRerankerNode"
description: "Optional native transformers cross-encoder reranker BAAI/bge-reranker-v2-m3."
updated: 2026-07-27
---
# BgeRerankerNode

`BgeRerankerNode` — optional cross-encoder reranking node на
`BAAI/bge-reranker-v2-m3`. Он не подключён в default pipeline; основной scoring
path использует `ParallelScoringNode`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `metadata.bge_reranker_max_score`, если reranking
успешен.

Если model/tokenizer/target roles/text недоступны, узел fail-open возвращает
record с `metadata.reranker_degradation`.

## Параметры

`target_roles: list[str]`.

`model_name = BAAI/bge-reranker-v2-m3`.

`revision` или env `JOB_FTCH_BGE_RERANKER_REVISION`; default `refs/pr/6`.

## Логика

Constructor пытается загрузить `transformers` tokenizer/model, переводит model
в eval mode и half/cuda при доступной CUDA.

На processing строятся пары `[role, title + description]`. Forward pass
запускается через `asyncio.to_thread`, чтобы не блокировать event loop
Telegram long-poll/runtime. Logits нормализуются sigmoid’ом; max score пишется
в metadata.

## Границы

Это optional precision/reranking enhancement, а не recall gate и не terminal
decision owner.
