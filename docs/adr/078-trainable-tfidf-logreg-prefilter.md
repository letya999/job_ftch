---
title: "ADR-078: Trainable TF-IDF + logistic regression prefilter over keyword rules and embeddings"
description: "Decision record for the trainable relevance prefilter used before the LLM judge."
date: 2026-07-26
updated: 2026-07-26
status: accepted
---

# ADR-078: Trainable TF-IDF + logistic regression prefilter

## Context

The LLM relevance judge processes every candidate that passes dedup and hard
filters. In the pre-recipe baseline measurement, a live run with 17 sources
meant 455 LLM calls at $0.33, with P=0.717 and F1=0.738. A cheap prefilter
that drops obvious negatives before the LLM can cut cost and improve precision
by removing noise from the judge's input.

This ADR records the design decision and the historical measurements that led
to it. The current pinned production recipe, commands, source set, graph/model
hashes, and guaranteed regression metrics are maintained in
[`docs/recipes/pipeline_recipe.md`](../recipes/pipeline_recipe.md) and
[`config/recipes/production_pipeline_recipe.yaml`](../../config/recipes/production_pipeline_recipe.yaml).

Three alternatives were measured on the same seed-42 sample of 400:

## Alternatives evaluated

### 1. Ontology keyword rules (best configuration)

Static keyword matching using the derived ontology vocabulary.

- Result: 180 LLM calls, 0.90 positive retention.
- Problem: the keyword vocabulary is hardcoded to the AI domain
  (`ontology_graph_builder.py`) and does not adapt to a different user's
  domain without code changes.

### 2. BGE-M3 shot embeddings at threshold 0.05

Dense+sparse embedding similarity against the user's positive/negative
shot examples.

- Result: 0 drops (every candidate passed) and 2812 ms per item.
- Problem: at any threshold that actually drops items, positive retention
  fell below 0.90. The embedding space does not separate relevant from
  irrelevant vacancies well enough for a binary gate.

### 3. TF-IDF + logistic regression (chosen)

Trained on the user's labelled dataset (1685 rows after excluding the
eval holdout, 216 positives).

- Result: 77 LLM calls, 0.96 positive retention.
- Historical holdout performance: P=0.957, R=0.815, F1=0.880 at 76 LLM calls.

## Decision

Use the TF-IDF + logistic regression prefilter. It is:

- **Domain-agnostic in code**: the model learns entirely from the dataset,
  with zero domain words in the node implementation.
- **Cheap at inference**: pure Python, no GPU, sub-millisecond per item.
- **Trainable**: a new user with a different domain can train their own
  model using `scripts/eval/train_relevance_prefilter.py` given a labelled
  dataset of 2000+ rows.
- **Safe on failure**: without a model artifact it degrades to pass-through,
  so every candidate reaches the LLM (costly but correct).

## Consequences

- The model artifact (`fixtures/prefilter/tfidf_logreg_v1.json`) must be
  present for the prefilter to function. Without it the pipeline works but
  at baseline cost and quality.
- sklearn is a training-time dependency only, not a runtime dependency.
- The ontology layer remains a separate component. It is learned from tenant
  shots through the compiled ontology contract in
  [`docs/ontology/compiler.md`](../ontology/compiler.md), while this prefilter
  is learned from the labeled eval dataset. Do not mix the two and do not add
  Python-side AI-domain vocabularies to either layer.
