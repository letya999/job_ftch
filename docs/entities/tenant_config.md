---
title: "TenantConfig (Конфигурация тенанта)"
description: "Текущая модель tenant-level конфигурации, изоляции источников и runtime overlay."
updated: 2026-07-28
---
# TenantConfig (Конфигурация тенанта)

`TenantConfig` — domain-модель одного tenant runtime: какие источники читать,
какие backend-ы использовать, как искать/хранить вакансии и в каком режиме
отправлять уведомления. Модель живёт в `job_ftch/domain/tenant.py`, YAML-файлы
загружает `job_ftch/application/tenant_loader.py`, а применяет их
`TenantRunner` из `job_ftch/application/tenant_runner.py`.

## Где лежит текущая конфигурация

Production/dev tenant-файлы для Telegram bot adapter лежат здесь:

- `job_ftch/adapters/telegram_bot/config/tenants/*.yaml`

CLI и MCP adapter получают этот каталог через `--configs-dir`. Глобальные
runtime-настройки берутся из `.env.*`, `config/runtime*.yaml` и, для бота,
`job_ftch/adapters/telegram_bot/runtime*.yaml`; подробная карта слоёв описана в
[runtime_and_env](../adapters/runtime_and_env.md).

## Что находится в TenantConfig

`TenantConfig` хранит только tenant-level wiring. Он не должен становиться
вторым `.env` и не должен держать секреты.

- `tenant_id`, `display_name` — идентификатор и человекочитаемое имя.
- `sources` — статические `SourceSpec`, которые входят в базовый ingest tenant-а.
- `source_backend` — основной backend источников, например Telegram или fixture.
- `store_backend`, `job_group_store_backend`, `job_backend` — durable storage.
- `search_backend`, `search_language` — режим поиска/дедупликации.
- `embedding_enabled`, `embedding_provider`, `embedding_model`,
  `embedding_dimensions`, `vector_backend` — vector enrichment, если он включён.
- `notify_mode`, `notify_batch_size` — режим уведомлений для sink/runtime adapter.
- Опциональные backend overrides и model overrides — tenant-level переопределения
  поверх базовых `Settings`.

Минимальный текущий пример похож на `ai_jobs.yaml`:

```yaml
tenant_id: ai_jobs
display_name: AI Jobs RU/KZ

source_backend: telegram_channel
sources: []

store_backend: postgres
job_group_store_backend: postgres
job_backend: postgres
search_backend: hybrid
search_language: russian

embedding_enabled: false
embedding_provider: openai
embedding_model: text-embedding-3-small
embedding_dimensions: 1536
vector_backend: qdrant

notify_mode: digest
notify_batch_size: 10
```

## Как TenantRunner применяет конфиг

`TenantRunner.from_tenants(...)` создаёт `TenantRuntime` на каждый tenant и
связывает конфиг с актуальными `Settings`, store, sink, pipeline graph,
source inputs и runtime state. На запуске tenant-а runner строит effective
source list, прогоняет ingest, pipeline stages и delivery/outbox.

Типовой Python-путь:

```python
from pathlib import Path

from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings

settings = Settings(configs_dir=Path("job_ftch/adapters/telegram_bot/config/tenants"))
tenants = load_tenants(settings.configs_dir)
runner = TenantRunner.from_tenants(tenants, base_settings=settings)

summary = await runner.run_tenant("ai_jobs")
```

## Изоляция tenant-ов

Один backend storage может обслуживать несколько tenant-ов. Изоляция достигается
не отдельным процессом на tenant, а tenant-aware ключами, runtime state и store
операциями. Курсоры источников, runtime sources, outbox/delivery state и
результаты привязаны к `tenant_id`, поэтому два tenant-а могут читать один и тот
же Telegram-канал, но иметь независимые курсоры и независимый набор результатов.

## RuntimeSource overlay

YAML — это базовая декларативная конфигурация. Динамические источники, добавленные
через Telegram bot, MCP или API, не записываются обратно в YAML. Они сохраняются
в tenant store как runtime records и при запуске объединяются с `sources`:

```python
record = await runner.add_source_spec(
    "ai_jobs",
    telegram_spec,
    added_via="telegram_bot",
)
```

Effective source set строится как базовые YAML-источники плюс enabled runtime
sources минус disabled/deleted runtime records. Это позволяет держать
инфраструктурный baseline в Git, а пользовательские добавления — в durable
runtime state.

## Границы ответственности

`TenantConfig` не выбирает конкретную реализацию pipeline graph вручную. Граф
приходит через `Settings.pipeline_graph_path` и builder/runtime layer; подробности
в [builder_and_graph](../pipelines/builder_and_graph.md).

`TenantConfig` также не оценивает качество источников. Source-level пригодность
проверяют `SourceAssessmentAdapter` и source assessment flow; см.
[source_assessment](../sources/source_assessment.md).

## Типичные ошибки

- Не храните секреты в tenant YAML: ключи API, DSN и токены должны приходить из
  `.env.*` или secret manager.
- Не перезаписывайте tenant YAML из runtime handler-ов: динамические добавления
  должны идти через `add_source_spec` и store.
- Не обходите `tenant_id` в кастомных store/sink: иначе runtime state разных
  tenant-ов начнёт смешиваться.
- Не считайте `sources: []` ошибкой: для Telegram bot tenant базовый список может
  быть пустым, а реальные источники приходят через runtime overlay.

## Связанные документы

- [SourceSpec](source_spec.md) — формат описания источников.
- [SourceAssessmentAdapter](source_assessment_adapter.md) — pre-ingest оценка источников.
- [PipelineBuilder](pipeline_builder.md) — сборка pipeline из recipe/graph.
- [Store](store.md) — durable storage и tenant-aware state.
- [RunSummary](run_summary.md) — результат tenant run.
