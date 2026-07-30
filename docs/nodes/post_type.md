---
title: "PostTypeClassificationNode"
description: "Ранняя классификация типа поста: job_posting / candidate / spam / announcement."
updated: 2026-07-27
---
# PostTypeClassificationNode

`PostTypeClassificationNode` присваивает raw item предварительный тип поста до
extraction: `job_posting`, `candidate_seeking`, `spam`, `announcement` или
`unknown`.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem` с metadata-полями post-type classification.

Узел не дропает item. Его результат использует `HardFilterNode` и поздняя
evidence/decision логика.

## Параметры

`classifier` — опциональный внешний `ClassifierProvider`.

`confidence_threshold` — минимальная уверенность внешнего classifier.

`announcement_tokens`, `job_posting_tokens`,
`job_posting_strong_tokens`, `candidate_tokens`, `spam_tokens` — keyword lists,
которые runtime загружает из classifier config.

## Логика приоритетов

Source-confirmed vacancy detail/structured record с
`detail_vacancy_confirmed = True` получает `job_posting` независимо от
случайных footer/token noise.

Candidate self-promotion распознаётся раньше широкого spam словаря.

Strong vacancy intent и shape-сигнал “роль + hiring context” переопределяют
incidental announcement words.

Внешний classifier используется только если rules не дали уверенный результат.
Если classifier ниже порога, узел возвращается к fallback keyword rules.

## Что пишет

`preclassified_post_type`, `preclassified_confidence`,
`preclassified_model`, `post_type_distribution`, `post_type_evidence`.

## Границы

Это ранняя типизация наблюдения, а не final relevance. Tenant-specific логика
“подходит пользователю” должна жить в profile/evidence/decision слоях.
