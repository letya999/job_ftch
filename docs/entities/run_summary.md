---
title: "RunSummary"
description: "**Слой**: `application`"
updated: 2026-07-24
---
# RunSummary

**Слой**: `application`
**Файл**: `job_ftch/application/pipeline.py`

## Что это

`RunSummary` — итоговая статистика одного pipeline run.

Он собирает как общие счётчики, так и разрезы по source kind и source identity.

## Из чего состоит

`RunSummary` наследует счётчики из `StatsBase`, включая:

- `fetched`
- `sanitized`
- `triaged`
- `extracted`
- `partial`
- `review`
- `duplicates`
- `dropped`
- `emitted`
- `posted`
- `rejected`
- `quarantined`
- `failed`
- `new_groups_created`
- `merged_into_group`

Дополнительно есть технические поля:

- `tenant_id`
- `applied_profile`
- `started_at`
- `finished_at`
- `scheduled_run_index`
- `source_run_id`
- `by_source_kind`
- `by_source_id`

## Для чего нужен

- итоговый вывод CLI
- run history в tenant runtime
- source health updates
- metrics/exporters
- пост-анализ качества ingestion

## Что важно понимать

- `RunSummary` считает не только успехи, но и drop/quarantine reasons
- у career-site path в нём могут появляться monitor/scraper-specific counters
- это runtime/statistics object, а не domain vacancy contract

## Связанные документы

- [Store](store.md)
- [PipelineBuilder](pipeline_builder.md)
- [TenantConfig](tenant_config.md)
