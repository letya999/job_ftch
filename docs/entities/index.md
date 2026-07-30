# entities Index

`docs/entities/`

Generated index for navigation. Edit source documents, then rerun `uv run python scripts/build_index_docs.py`.

## Files On This Level

- [Adapters and plugins](adapters_and_plugins.md) - `job_ftch` uses several extension shapes. The names are intentionally (Updated: 2026-07-24)
- [Application Model Map](application_model_map.md) - Полный индекс `job_ftch/application/*`: composition roots, ports, runtime orchestration, registries и helper modules. (Updated: 2026-07-28)
- [AuthProvider](auth_provider.md) - **Слой**: `application` (Updated: 2026-07-24)
- [Backends (Бэкенды хранения вакансий)](backend.md) - Backends в проекте `job_ftch` — это собирательное название (Updated: 2026-07-24)
- [BypassStrategy (Стратегия обхода защит)](bypass_strategy.md) - BypassStrategy — это слой абстракции над HTTP-клиентом или (Updated: 2026-07-24)
- [CandidateProfile](candidate_profile.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [Career Site Engines](career_site_engines.md) - Monitor, scraper, site parser и bypass роли внутри career-site ingestion. (Updated: 2026-07-28)
- [Domain Model Map](domain_model_map.md) - Полный индекс `job_ftch/domain/*` на текущий момент. (Updated: 2026-07-28)
- [JobDraft](job_draft.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [JobGroup](job_group.md) - **Слой**: domain (Updated: 2026-07-24)
- [Job Group Store](job_group_store.md) - `JobGroupStore` — это специализированное хранилище (база (Updated: 2026-07-24)
- [JobRecord](job_record.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [LLMProvider](llm_provider.md) - **Слой**: `application` (Updated: 2026-07-24)
- [ML & Infrastructure Providers](ml_and_infra_providers.md) - `job_ftch` использует портовую архитектуру (Ports and Adapters). (Updated: 2026-07-24)
- [PipelineBuilder](pipeline_builder.md) - **Слой**: `application` (Updated: 2026-07-28)
- [Plugin (Плагин и реестр плагинов)](plugin.md) - Plugin (плагин) — это механизм регистрации и discovery (Updated: 2026-07-24)
- [Система плагинов (Plugins)](plugins.md) - **Слой**: application (Updated: 2026-07-24)
- [ProfileCatalog](profile_catalog.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [Базовые протоколы](protocols.md) - **Слой**: `application` (Updated: 2026-07-24)
- [RawItem](raw_item.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [Сущности и контракты](README.md) - Ключевые entity-карточки плюс полные карты domain/application модулей. (Updated: 2026-07-28)
- [RunSummary](run_summary.md) - **Слой**: `application` (Updated: 2026-07-24)
- [RuntimeAdapter (Рантайм-адаптеры)](runtime_adapters.md) - Runtime adapter — это внешний слой, который подключает library-first core (Updated: 2026-07-24)
- [Sink](sink.md) - `Sink` — финальная точка назначения для pipeline output. (Updated: 2026-07-24)
- [Source](source.md) - `Source` — это поставщик входящих элементов для пайплайна. (Updated: 2026-07-24)
- [SourceAssessmentAdapter](source_assessment_adapter.md) - Pre-ingest contract для оценки capabilities, freshness и bypass needs источника. (Updated: 2026-07-28)
- [SourceSpec](source_spec.md) - **Слой**: `domain` (Updated: 2026-07-24)
- [Stage / Node](stage_node.md) - **Слой**: `application` + `nodes/` (Updated: 2026-07-28)
- [Store](store.md) - `Store` — operational persistence layer пайплайна. (Updated: 2026-07-24)
- [TenantConfig (Конфигурация тенанта)](tenant_config.md) - Текущая модель tenant-level конфигурации, изоляции источников и runtime overlay. (Updated: 2026-07-28)
- [VacancyFeedback](vacancy_feedback.md) - **Слой**: `domain` + `application` (Updated: 2026-07-24)
