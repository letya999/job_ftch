---
title: "PipelineBuilder"
description: "**Слой**: `application`"
updated: 2026-07-28
---
# PipelineBuilder

**Слой**: `application`
**Файл**: `job_ftch/application/builder.py`

## Что это

`PipelineBuilder` — fluent builder для сборки `Pipeline`.

Он собирает sources, stages, sinks, store и runtime side channels в валидную
композицию.

## Что умеет

- задавать один или несколько sources
- подключать `AuthProvider`
- задавать `Store`
- добавлять stages в порядке исполнения
- подключать main sink, quarantine sink и rejected sink
- настраивать snapshot filter path
- запускать pipeline через `run_async()`

## Важная валидация

`build()` не соберёт pipeline, если:

- не задан ни один source
- не задан ни один stage
- первый stage не `SanitizeNode`
- не задан ни один sink

Это не stylistic choice, а enforced runtime invariant.

## Snapshot behavior

Если builder собирается с `run_id`, `SnapshotFilterNode` становится вторым
stage и передаётся отдельно в `Pipeline`, чтобы оркестратор мог вызвать
`save_and_purge()` в конце run.

## Где builder реально используется

- library/single-run path
- `configure()` helpers
- `TenantRunner`
- CLI and adapter composition roots

## Что не делать

- не использовать builder как глобальное mutable shared state между request-like runs
- не вставлять stages перед `SanitizeNode`
- не обходить builder direct wiring, если нужна стандартная pipeline semantics

## Связанные документы

- [Protocols](protocols.md)
- [RunSummary](run_summary.md)
- [SourceSpec](source_spec.md)
- [PipelineBuilder, Pipeline и Graph](../pipelines/builder_and_graph.md)
