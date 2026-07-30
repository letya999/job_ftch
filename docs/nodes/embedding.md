---
title: "EmbeddingNode"
description: "JobRecord embedding + batched vector backend upsert."
updated: 2026-07-27
---
# EmbeddingNode

`EmbeddingNode` строит vector embedding для `JobRecord`, кладёт vector в
metadata и батчит upsert в vector backend.

## Вход и выход

**Вход:** `JobRecord` с `group_id` в поле или metadata.

**Выход:** `JobRecord` с `metadata.embedding_vector`, если embedding успешен.

Если `group_id` отсутствует, узел бросает `ValueError`: vector запись должна
быть привязана к group identity.

## Зависимости

`EmbeddingProvider` — используется `embed_passage()` или fallback `embed()`.

`VectorBackend` — должен поддерживать `upsert_many()` или per-item `upsert()`;
optional `ensure_collection(dim=...)`.

## Логика

Текст строится через `build_job_embedding_text(job)`. Collection lazily
создаётся на первом успешном vector write. Upserts накапливаются в памяти до
`upsert_batch_size`, затем flush’атся.

`flush()` нужно вызвать в конце run, чтобы записать неполный batch.

## Границы

Embedding/vector backend — optional infrastructure enhancement. Ошибка embed
или upsert логируется warning’ом и не останавливает pipeline.
