---
title: "EmbeddingPrefilterNode"
description: "Legacy cross-lingual role-anchor prefilter на embedding provider."
updated: 2026-07-27
---
# EmbeddingPrefilterNode

`EmbeddingPrefilterNode` сравнивает embedding текста raw item с embeddings
`target_roles` из профилей. Это legacy/compatibility prefilter; основной
semantic path движется к BGE-M3 dense+sparse metadata и shot-anchor scoring.

## Вход и выход

**Вход:** `RawItem` и `ProfileCatalog`.

**Выход:** `RawItem` с metadata `embedding_role_match`,
`embedding_role_best_profile`, `embedding_prefilter_decision`.

Узел fail-safe: при отсутствии profiles, embed function, role anchors или при
ошибке provider возвращает item без drop.

## Параметры

`pass_threshold = 0.50`, `drop_threshold = 0.35`, `max_chars = 512`.

`embedding_provider` должен иметь `embed_query()` или `embed()`.

## Логика

Role vectors строятся лениво один раз по `target_roles` каждого профиля.
Текст item обрезается до `max_chars`, embedding сравнивается с role vectors
через cosine similarity. Лучший score и profile id пишутся в metadata.

Decision metadata: `pass`, `low_signal` или `uncertain`.

## Границы

Даже `low_signal` здесь не равен terminal reject. Это дополнительный
cross-lingual signal для downstream semantic/evidence stages.
