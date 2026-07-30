---
title: "PipelineBuilder, Pipeline и Graph"
description: "Как соотносятся PipelineBuilder, Pipeline, YAML graph, GraphPipelineStage и TenantRunner."
updated: 2026-07-28
---
# PipelineBuilder, Pipeline и Graph

В проекте есть несколько похожих слов: builder, pipeline, graph, tenant
runtime. Они не взаимозаменяемы. Этот документ фиксирует, кто за что отвечает
и где проходит граница между orchestration и item-level processing.

## Главная модель

```text
TenantConfig / CLI / Python API
  -> Settings + SourceSpec + sinks + store
  -> PipelineBuilder
  -> Pipeline
  -> GraphPipelineStage
  -> compiled graph nodes
  -> sinks / outbox / run summary
```

`PipelineBuilder` собирает runtime. `Pipeline` исполняет run lifecycle.
Graph описывает обработку одного item. `TenantRunner` добавляет multi-tenant
оркестрацию, runtime overlays, source health, profiles и ontology.

## PipelineBuilder

Файл: `job_ftch/application/builder.py`.

Builder отвечает за composition:

- sources или готовый runtime source;
- store и tenant-scoped store wrapper;
- main sink, review sink, rejected sink, quarantine sink;
- delivery targets;
- sanitizer и processing stages;
- snapshot filter wiring;
- concurrency и source pool runtime;
- summary context.

Важные инварианты builder:

- source обязателен;
- stage обязателен;
- первый stage должен быть `SanitizeNode`;
- sink обязателен;
- snapshot filter, если включён, становится вторым stage;
- runtime graph не должен содержать legacy terminal stages.

Builder не должен быть глобальным mutable state между независимыми runs.

## Pipeline

Файл: `job_ftch/application/pipeline.py`.

Pipeline отвечает за lifecycle одного запуска:

- читает source iterator;
- применяет sanitizer;
- применяет processing nodes;
- изолирует item-level failures;
- ведёт counters и `RunSummary`;
- пишет в sinks;
- обслуживает quarantine/rejected side channels;
- вызывает snapshot `save_and_purge`;
- закрывает run state.

Pipeline не должен знать, почему item accepted/rejected по релевантности.
Это область graph/decision nodes.

## Graph и GraphPipelineStage

Graph manifests лежат в `config/pipelines/*.yaml`. Generated inventory:
[graphs.md](graphs.md).

Graph описывает item-level path:

- node id;
- input/output type;
- dependencies/order;
- effect;
- execution phase;
- params;
- graph hash.

Активный runtime graph выбирается через `Settings.pipeline_graph_path`.
Production и dev overlays сейчас задают:
`config/pipelines/evidence_v2_compact_prefilter.yaml` +
`pipeline_graph_expected_hash =
0d73de0663d220da62e37d9a41159542547d167f9f096088f7ae85ec587e44fb`.
TenantRunner валидирует hash перед сборкой executor.

`GraphPipelineStage` адаптирует compiled graph к обычному `Stage`, чтобы
Pipeline мог исполнять graph как один processing stage. Это важная граница:
Pipeline остаётся lifecycle-orchestrator, а graph остаётся item processor.

## TenantRunner

Файл: `job_ftch/application/tenant_runner.py`.

TenantRunner отвечает за long-lived runtime:

- загрузку tenants;
- runtime source overlays;
- pre-ingest source assessment;
- source health/pause/probe;
- candidate profiles;
- ontology snapshot;
- graph hash validation;
- tenant-scoped stores;
- запуск одного tenant или всех tenants.

TenantRunner не должен превращаться в hidden orchestrator поверх внешних
процессов. Он оркестрирует runtime библиотеки внутри процесса.

## Где живёт конфигурация

- `job_ftch/config.py` — Settings, defaults, validation.
- `config/runtime.yaml` — base runtime policy.
- `config/runtime.dev.yaml` — dev overrides.
- `config/runtime.prod.yaml` — prod overrides.
- `config/pipelines/*.yaml` — graph manifests.
- `job_ftch/adapters/telegram_bot/config/tenants/*.yaml` — tenant config.
- `.env*.example` — env templates for secrets/DSNs/runtime wiring.
- `job_ftch/adapters/telegram_bot/runtime*.yaml` — bot-specific runtime
  overlays used by compose/adapter commands.

## Как проверять изменения

- graph/reference docs: `uv run python scripts/build_graph_reference.py`;
- generated docs check: `uv run python scripts/check_docs_generated.py`;
- config layer check: `uv run python scripts/check_config_layers.py`;
- module boundaries: `uv run python scripts/check_module_boundaries.py`;
- recipe regressions: `uv run pytest tests/eval/test_champion_recipe.py tests/eval/test_production_recipe.py -q`.

## Связанные документы

- [PipelineBuilder](../entities/pipeline_builder.md)
- [Graph control-flow](graph_control_flow.md)
- [Relevance funnel](relevance_funnel.md)
- [Generated pipeline graph reference](graphs.md)
- [Пайплайн фильтрации и отбора вакансий](filtering_pipeline.md)
- [Node Catalog](../nodes/README.md)
- [Рецепт production-пайплайна](../recipes/pipeline_recipe.md)
