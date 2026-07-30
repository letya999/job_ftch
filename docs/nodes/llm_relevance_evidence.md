---
title: "LLMRelevanceEvidenceNode"
description: "Typed graph adapter: AssessedJob + LLM relevance evidence -> AssessedJob."
updated: 2026-07-27
---
# LLMRelevanceEvidenceNode

`LLMRelevanceEvidenceNode` адаптирует legacy `LLMRelevanceClassificationNode`
к typed evidence graph.

## Вход и выход

**Вход:** `AssessedJob`.

**Выход:** `AssessedJob` с обновлённым `record`, merged `evidence`,
пересчитанными `assessments` и optional degradation reason.

Если на вход пришёл не `AssessedJob`, узел бросает `TypeError`.

## Логика

Внутри узел вызывает classifier на `item.record`. Затем читает serialized
`evidence_atoms` из record metadata, валидирует их как `EvidenceAtom`, merging
по `evidence_id` с уже существующими atoms.

После merge вызывает `aggregate_bundle()` и возвращает новый assessed job.

Если classifier вернул `None`, добавляется degradation
`llm_relevance_empty`. Если record содержит `llm_relevance_degradation`,
добавляется `llm_relevance_unavailable`.

## Границы

Это adapter для typed graph, а не отдельная LLM implementation. Prompt/cache
поведение описано в [LLMRelevanceClassificationNode](llm_relevance_classification.md).
