---
title: "SemanticPrefilterNode"
description: "Profile-aware weak relevance evidence до дорогого extraction/LLM."
updated: 2026-07-27
---
# SemanticPrefilterNode

`SemanticPrefilterNode` оценивает raw item против `ProfileCatalog` до дорогих
стадий. В текущем pipeline это weak evidence/prefilter stage: он помечает
uncertainty и score, а не является владельцем финального accept/reject.

## Вход и выход

**Вход:** `RawItem` после source context, post type и optional embedding/BGE.

**Выход:** `RawItem` с profile relevance metadata.

## Режимы

Без `relevance_scorer` используется token/phrase overlap по профилям:
target roles, domains, hard requirements, soft preferences, anti preferences и
profile description bonus.

С `relevance_scorer` используется shot-anchor scoring. Если scorer умеет
`score_from_metadata`, он читает precomputed BGE metadata; иначе может вызвать
`score_text`. Blocking encode выносится в thread через `asyncio.to_thread`.

## Bypass и rescue

Source-confirmed `VACANCY_DETAIL` с `detail_vacancy_confirmed = True` bypass’ит
weak token prefilter. Career-site source kind сам по себе не bypass, потому что
это может быть listing/search/category page.

Для shot scoring есть rescue logic: `strong_ai_signal` или `any_positive`.
Низкий margin не должен убивать вакансию, если есть сильный AI-building signal
или upstream positive evidence.

## Что пишет

Token mode: `semantic_prefilter_best_profile`,
`semantic_prefilter_best_score`, `semantic_prefilter_scores`,
`semantic_prefilter_uncertain`.

Shot mode: `semantic_prefilter_shot_margin`,
`semantic_prefilter_uncertain`, optional `semantic_prefilter_override`.

## Границы

Узел не делает финальный accept/reject. Его результат должен быть входом для
evidence decision, а не единственной правдой о релевантности.
