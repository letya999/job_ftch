---
title: "SkillNormalizationNode"
description: "Нормализация explicit/inferred skills через Normalizer."
updated: 2026-07-27
---
# SkillNormalizationNode

`SkillNormalizationNode` находится в `job_normalization.py` и приводит skills к
canonical форме после extraction.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord`.

## Логика

Узел вызывает injected `Normalizer.normalize_skills()` отдельно для
`skills_explicit` и `skills_inferred`.

Если результат отличается от исходного record, обновляет оба поля и добавляет
`skills:normalized` в `provenance.normalization`.

Если изменений нет, возвращает record как есть.

## Границы

Узел не infer’ит новые skills из description и не пересчитывает match score.
Он только нормализует уже извлечённые skill tags.
