---
title: "JobValidationNode"
description: "Graph id для structural/legacy policy validation JobRecord."
updated: 2026-07-27
---
# JobValidationNode

`job_validation` — graph id для `JobValidationNode` из `quality.py`.

## Контракт

**Вход:** `JobRecord`.

**Выход:** `JobRecord`.

**Drop:** `RawItemDropped`, если record структурно непригоден или legacy policy
enforcement отклоняет его по quality/relevance/profile decisions.

## Production note

В production graph runtime policy должен принадлежать `EvidenceDecisionNode`.
Поэтому `JobValidationNode` может работать structural-only через
`enforce_policy = False`.

См. [QualityScoringNode и JobValidationNode](quality.md).
