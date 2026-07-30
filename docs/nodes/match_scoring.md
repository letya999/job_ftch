---
title: "MultiProfileMatchNode"
description: "Deterministic multi-profile scoring для normalized JobRecord."
updated: 2026-07-27
---
# MultiProfileMatchNode

`MultiProfileMatchNode` считает deterministic match scores между `JobRecord` и
каждым `SearchProfile` из `ProfileCatalog`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `profile_scores`, `relevance_score`,
`best_profile_id`, `best_score` и metadata `hard_constraint_states`.

Если profiles нет, узел выставляет `relevance_score = 1.0` и пустые
`profile_scores`, чтобы source без профилей не был искусственно отфильтрован.

## Логика scoring

Для каждого профиля считаются компоненты: semantic role/title, skills, domain,
seniority, region, salary, culture, vector score и vacancy type score.

Hard constraints не трактуют отсутствие данных как contradiction. Например,
если language или seniority неизвестны, это `unknown`, а не automatic reject.

Vector score берётся из `metadata.embedding_vector` и profile centroid, либо
fallback из `metadata.embedding_role_match`.

Risk signals и negative vector similarity уменьшают final score.

## Decision per profile

Каждый `ProfileMatchScore` получает `ACCEPT`, `REVIEW` или `REJECT` по
собственному calibrated threshold профиля. Глобальный best выбирается не просто
по максимуму raw score: сначала any ACCEPT, затем any REVIEW, затем rejects.

## Границы

Узел не является final policy owner. Его per-profile scores превращаются в
evidence и/или используются compatibility aggregators downstream.
