---
title: "QualityScoringNode и JobValidationNode"
description: "Structured quality score и minimum usefulness validation для JobRecord."
updated: 2026-07-27
---
# QualityScoringNode и JobValidationNode

`quality.py` содержит два разных узла: quality scoring и structural/policy
validation.

## QualityScoringNode

**Вход/выход:** `JobRecord -> JobRecord`.

Считает `quality_score` по наличию title, company, canonical URL, location,
work mode, compensation, длине description и explicit skills. Risk signals
уменьшают score.

Если score ниже `review_threshold = 0.6`, добавляет review reason
`LOW_QUALITY_SCORE`. Узел не дропает record.

## JobValidationNode

**Вход:** `JobRecord`.

**Выход:** `JobRecord`, если validation проходит.

**Drop:** `RawItemDropped`, если нет ни title, ни company, ни canonical URL;
или если включена policy enforcement и score/relevance/profile decisions ниже
минимума.

Параметры: `min_quality_score`, `min_relevance_score`, `llm_band_floor`,
`enforce_policy`.

Если `enforce_policy = False`, production policy остаётся у
`EvidenceDecisionNode`, а этот узел выполняет только structural validation.

## Границы

Quality score — это не relevance. Validation может быть legacy policy gate,
но в production graph финальное runtime decision принадлежит evidence policy.
