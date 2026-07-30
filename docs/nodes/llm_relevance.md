---
title: "Graph node: llm_relevance"
description: "Graph id для LLMRelevanceClassificationNode."
updated: 2026-07-27
---
# Graph node: `llm_relevance`

`llm_relevance` — graph id для `LLMRelevanceClassificationNode`.

Контракт: `JobRecord -> JobRecord`. Узел пишет `_llm_relevance`,
LLM/evidence metadata и degradation markers, но сам не должен быть единственным
terminal routing owner.

См. [LLMRelevanceClassificationNode](llm_relevance_classification.md).
