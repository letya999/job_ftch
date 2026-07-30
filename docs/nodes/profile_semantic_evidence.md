---
title: "ProfileSemanticEvidenceNode"
description: "Semantic margin между BGE vacancy vector и profile intent/anti-intent queries."
updated: 2026-07-27
---
# ProfileSemanticEvidenceNode

`ProfileSemanticEvidenceNode` сравнивает уже embedded vacancy vector с cached
positive и negative profile query vectors.

## Вход и выход

**Вход:** `JobRecord` с `metadata.bgem3_dense`.

**Выход:** `JobRecord` с `profile_semantic_positive`,
`profile_semantic_negative`, `profile_semantic_margin`.

Если `bgem3_dense` отсутствует, узел no-op.

## Параметры

`positive_vectors: np.ndarray` — query vectors намерения профиля.

`negative_vectors: np.ndarray` — anti-intent query vectors.

## Логика

Узел не re-encode’ит вакансию. Он берёт dense vector из metadata, считает max
dot product с positive vectors и max dot product с negative vectors. Margin =
positive - negative.

## Границы

Это evidence feature, а не routing decision. Negative semantic margin должен
попадать в aggregator/decision как conflict, а не сам дропать record.
