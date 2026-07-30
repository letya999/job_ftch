---
title: "ADR 038: Native Transformers implementation for BGE Reranker"
description: "ACCEPTED"
updated: 2026-07-24
---
# ADR 038: Native Transformers implementation for BGE Reranker

## Status
ACCEPTED

## Context
In Phase 3 of the `job_ftch` pipeline, we introduced `BgeRerankerNode` which initially utilized `FlagReranker` from the `FlagEmbedding` package to compute cross-encoder scores against target roles.
However, during pipeline evaluation, this caused a 100% crash rate with `AttributeError: 'XLMRobertaTokenizer' object has no attribute 'prepare_for_model'`.
This error stems from the fact that `transformers` version 4.45+ removed the deprecated `prepare_for_model` method, which newer code paths in `FlagEmbedding` rely on.

Downgrading `transformers` to `4.44.2` globally would compromise other parts of the system (like LLM extraction and other local embeddings) that benefit from or require newer versions. Monkey-patching `XLMRobertaTokenizer` is brittle and risks silent tokenization failures.

## Decision
We bypass `FlagEmbedding` for cross-encoder inference entirely. Instead, we use native `transformers.AutoModelForSequenceClassification` and `AutoTokenizer` directly inside `BgeRerankerNode`.
- We initialize the model natively.
- We run standard inference with `padding=True`, `truncation=True`, and `max_length=512`.
- We compute the final score by applying a sigmoid over the logits to replicate `normalize=True`.

## Consequences
- **Robustness**: The node is fully compatible with modern `transformers` versions (5.x+).
- **Control**: Tokenization and device placement are explicit and safe.
- **Maintainability**: Reduced reliance on the `FlagEmbedding` library abstractions, giving us cleaner runtime evaluation without version conflicts.
