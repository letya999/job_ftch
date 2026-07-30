---
title: "HardFilterNode"
description: "Evidence-node для hard constraints: post type, language, blocked companies."
updated: 2026-07-27
---
# HardFilterNode

`HardFilterNode` фиксирует нарушения hard constraints в metadata/evidence, но
не делает terminal drop. Ранняя типизация может ошибаться, поэтому узел пишет
объяснимое отрицательное evidence и оставляет возможность downstream rescue.

## Вход и выход

**Вход:** `RawItem` после `PostTypeClassificationNode` и source/language
context.

**Выход:** `RawItem`; либо без изменений, либо с `hard_filter_evidence`,
`early_triage_state` и `evidence_atoms`.

## Что проверяет

`preclassified_post_type`. `candidate_seeking`, `announcement` и `spam` дают
hard evidence. Для `announcement` есть override, если текст явно похож на AI
job: AI role signal плюс job structure.

`detected_language`. Если ни один profile не задаёт `allowed_languages`, узел
пермишивный и пропускает все языки. Если allow-list есть, неизвестный язык всё
равно допускается как detector-uncertain.

`blocked_companies` из каждого профиля в `ProfileCatalog`.

## Evidence

На каждую причину создаётся `EvidenceAtom` с `claim=HARD_CONSTRAINT`,
`polarity=CONTRADICTS`, producer `hard_filter`, reliability `0.9`.

## Границы

Узел не должен становиться свалкой relevance heuristics. Всё, что требует
взвешивания, confidence, tenant preference или rescue, должно быть evidence и
решаться в decision layer.
