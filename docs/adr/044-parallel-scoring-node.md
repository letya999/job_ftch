---
title: "ADR-044: ParallelScoringNode"
description: "Status: SUPERSEDED-BY-058"
updated: 2026-07-24
---
# ADR-044: ParallelScoringNode

Status: SUPERSEDED-BY-058
Date: 2026-06-22

## Context

MultiProfileMatchNode computed profile match scores sequentially. BGE vector scoring
and keyword scoring were coupled. Need: (a) parallelism, (b) separation of signal types.

## Decision

ParallelScoringNode runs BGE embedding similarity and keyword relevance scoring in
parallel (asyncio.gather). Scores stored in item.metadata["parallel_final_score"].
LLMRelevanceClassificationNode reads parallel_final_score when available (prefers it
over relevance_score from MultiProfileMatchNode).

ParallelScoringNode is optional in the pipeline: builder includes it only when
bgem3_enabled=True in settings.

## Consequences

Faster scoring on items that reach this stage. BGE score and keyword score visible
separately in metadata for diagnostics. LLM call gating uses more accurate combined score.
