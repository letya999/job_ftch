---
title: "NeedMoreEvidenceNode"
description: "Выбор конкретного missing/uncertain claim для deferred resolver."
updated: 2026-07-27
---
# NeedMoreEvidenceNode

`NeedMoreEvidenceNode` выбирает самый приоритетный unresolved claim, когда
`DecisionNode` вернул deferred work state.

## Вход и выход

**Вход:** `AssessedJob`.

**Выход:** `AssessedJob`; если выбран uncertain claim, в record metadata
пишется `missing_critical_claim`.

Узел не создаёт terminal decision и не меняет routing.

## Приоритеты

1. `IS_JOB`
2. `PROFILE_RELEVANCE`
3. `HARD_CONSTRAINT`
4. `FRESHNESS`
5. `RISK`

Для каждого claim выбираются assessments с `certainty < 0.65`; из них берётся
минимальная certainty.

## Границы

Это diagnostic/deferred routing metadata для будущего resolver task. Он не
должен сам вызывать LLM или source recrawl.
