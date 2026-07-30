---
title: "Сущности и контракты"
description: "Ключевые entity-карточки плюс полные карты domain/application модулей."
updated: 2026-07-28
---
# Сущности и контракты

Этот раздел описывает ключевые типы, порты и runtime-контракты, вокруг которых
собран `job_ftch`.

Покрытие раздела строится в два слоя:

- детальные страницы для стабильных публичных сущностей и портов;
- полные карты модулей [Domain model map](domain_model_map.md) и
  [Application model map](application_model_map.md), чтобы ни один актуальный
  domain/application module не оставался невидимым в документации.

## Главная линия данных

```text
RawItem -> JobDraft -> JobRecord -> JobGroup
```

- `RawItem` — сырой вход из source
- `JobDraft` — структурированный черновик после extraction
- `JobRecord` — канонический публичный контракт
- `JobGroup` — агрегированная группа одинаковых вакансий из нескольких sources

## Главные протоколы

- [Source[T]](protocols.md) — поставщик входящих элементов
- [Stage[In, Out]](protocols.md) — узел обработки или type-changing stage
- [Sink[T]](protocols.md) — финальный вывод
- [Store](store.md) — состояние дедупликации, snapshots и run state
- [LLMProvider](llm_provider.md) — boundary для extraction / classification / presentation
- [AuthProvider](auth_provider.md) — boundary для разрешения секретов
- [SourceAssessmentAdapter](source_assessment_adapter.md) — pre-ingest оценка source capabilities

## Главные runtime-сущности

- [SourceSpec](source_spec.md) — конфигурация источника
- [CandidateProfile](candidate_profile.md) — профиль кандидата
- [ProfileCatalog](profile_catalog.md) — набор `SearchProfile` для матчинга
- [RunSummary](run_summary.md) — статистика одного запуска
- [VacancyFeedback](vacancy_feedback.md) — вердикты читателей на опубликованные вакансии
- [PipelineBuilder](pipeline_builder.md) — fluent builder для сборки pipeline
- [TenantConfig](tenant_config.md) — конфигурация tenant runtime
- [Domain model map](domain_model_map.md) — полный каталог `job_ftch/domain/*`
- [Application model map](application_model_map.md) — полный каталог `job_ftch/application/*`
- [Node catalog](../nodes/README.md) — полный каталог `job_ftch/nodes/*`

## Ключевые инварианты

- `SanitizeNode` всегда первый.
- `SnapshotFilterNode`, если включён, всегда второй.
- `domain/` не импортирует ничего, кроме stdlib и `pydantic`.
- Type changes делаются только через `Stage[In, Out]`.
- Финальные sinks должны получать `JobRecord`, а не `JobDraft`.

## Рекомендуемый порядок чтения

1. [RawItem](raw_item.md)
2. [JobDraft](job_draft.md)
3. [JobRecord](job_record.md)
4. [Protocols](protocols.md)
5. [Adapters and plugins](adapters_and_plugins.md)
6. [SourceSpec](source_spec.md)
7. [SourceAssessmentAdapter](source_assessment_adapter.md)
8. [Store](store.md)
9. [RunSummary](run_summary.md)
10. [PipelineBuilder](pipeline_builder.md)
11. [Domain model map](domain_model_map.md)
12. [Application model map](application_model_map.md)
13. [Node catalog](../nodes/README.md)
