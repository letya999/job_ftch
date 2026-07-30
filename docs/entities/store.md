---
title: "Store"
description: "`Store` — operational persistence layer пайплайна."
updated: 2026-07-24
---
# Store

## Что это

`Store` — operational persistence layer пайплайна.

Он не равен `JobPersistenceBackend`. `Store` хранит состояние обработки, а не
долгосрочный каталог вакансий.

## За что отвечает Store

- processed-item state
- dedup keys
- duplicate records
- arbitrary run state
- cached source strategies
- source snapshots для `SnapshotFilterNode`

В multi-tenant режиме поверх него строится namespaced `TenantStore`.

## Основные методы

- `has_processed` / `mark_processed`
- `has_dedup_key` / `remember_dedup_key`
- `list_dedup_keys`
- `record_duplicate` / `list_duplicate_records`
- `get_run_state` / `set_run_state`
- `get_source_strategy` / `save_source_strategy`
- `get_last_run_snapshot`
- `save_snapshot_rows`
- `purge_old_snapshots`

## Что не путать

### `Store`

Хранит operational state pipeline.

### `JobPersistenceBackend`

Хранит финальные `JobRecord` для выдачи, поиска и UI.

Это разные порты и разные уровни ответственности.

## Реализации

В репозитории есть как минимум:

- `InMemoryStore`
- `SQLiteStore`
- `PostgreSQLStore`
- `TenantStore` как namespaced façade

## Связанные документы

- [RunSummary](run_summary.md)
- [Store backend and persistence entities](backend.md)
- [PipelineBuilder](pipeline_builder.md)
