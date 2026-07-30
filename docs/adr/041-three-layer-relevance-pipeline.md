---
title: "ADR-041: Three-layer relevance pipeline"
description: "Status: ACCEPTED"
updated: 2026-07-24
---
# ADR-041: Three-layer relevance pipeline

Status: ACCEPTED
Date: 2026-06-22

## Context

Needed to distinguish: (1) obvious garbage, (2) borderline relevant,
(3) clearly relevant. Single-threshold LLM approach missed recall on
RU-language posts and wasted LLM tokens on garbage.

## Decision

Three-layer pipeline:
1. GarbageFilterNode — cheapest gate: URL/domain blocklist, spam signals. Drops obvious
   non-jobs before any LLM calls.
2. Shot-grounded SemanticPrefilterNode — keyword+embedding overlap against profile shots
   from Qdrant (profile_shots_e5 collection). Fallback to keywords when Qdrant unavailable.
3. LLMRelevanceClassificationNode — binary OpenAI judge (accept/reject) for borderline
   items in (low_threshold, high_threshold) window. Uses generated_relevance_prompt.txt
   loaded from disk via DI. Cost-controlled via max_per_run.

## Consequences

R≥0.7 achieved on gold_eval dataset (456 items). GarbageFilterNode removes ~30% of
items before LLM stage, reducing token cost proportionally.
