---
title: "Application Model Map"
description: "Полный индекс `job_ftch/application/*`: composition roots, ports, runtime orchestration, registries и helper modules."
updated: 2026-07-28
---
# Application Model Map

Полный индекс `job_ftch/application/*` на текущий момент. Этот файл закрывает
покрытие application layer в документации и дополняет детальные entity-docs.

## Composition и runtime

| Module | Role |
|---|---|
| `builder` | composition root для single-run/library pipeline и стандартной сборки stages/sinks/store |
| `pipeline` | lifecycle одного run: source iteration, nodes, sinks, side channels, summary |
| `tenant_runner` | multi-tenant runtime orchestration, source overlays, source health, graph execution |
| `tenant_runtime` | tenant-scoped runtime container |
| `tenant_store` | namespace-wrapper над Store для tenant isolation |
| `tenant_loader` | загрузка tenant YAML |
| `tenant_locks` | синхронизация tenant-level операций |
| `scheduler` | периодический запуск pipeline/runtime задач |

## Contracts, registry, plugins

| Module | Role |
|---|---|
| `contracts` | application ports: Source, Stage, Sink, Store, AuthProvider, LLMProvider и расширения |
| `registry` | создание registered sources/sinks/stores/LLM/backends |
| `plugin` | plugin descriptor/runtime helpers |
| `plugin_registry` | plugin discovery and registration helpers |
| `auth` | auth provider implementations/boundaries |

## Source input и assessment

| Module | Role |
|---|---|
| `source_loader` | загрузка source fixtures/specs из файлов |
| `source_inputs` | нормализация source input blocks и runtime source composition |
| `source_assessment` | application-level source assessment orchestration |
| `site_parser_manifest` | manifest support для site parser catalog |
| `watermark` | watermark helpers для incremental ingest |

## Profiles, ontology, prompts, shots

| Module | Role |
|---|---|
| `filter_profile_loader` | загрузка profile catalog/filter profiles |
| `profile_inputs` | input contracts для candidate profile data |
| `profile_parsing` | parsing candidate profile/resume inputs |
| `resume_extraction` | extraction path для resume/profile inputs |
| `prompt_builder` | runtime prompt construction |
| `ontology_compiler` | compile labeled shots в ontology/projection |
| `ontology_corpus` | corpus helpers для ontology compilation |
| `ontology_enrichment` | ontology enrichment helpers |
| `ontology_graph_builder` | graph projection/building из compiled ontology |
| `ontology_snapshot` | runtime ontology snapshot assembly |
| `shot_sync` | синхронизация profile shots with runtime stores |

## Decisions, evidence, drops, rejections

| Module | Role |
|---|---|
| `evidence_policy` | policy config/helpers для evidence decision |
| `drops` | controlled drop contracts/reasons |
| `rejections` | rejection contracts/reasons |
| `garbage` | garbage classification helpers |
| `identity` | identity helpers for items/jobs/groups |
| `resolver` | deferred/review resolver helpers |
| `shadow` | shadow/eval comparison support |

## Delivery, publication, observability helpers

| Module | Role |
|---|---|
| `delivery` | durable delivery target abstraction |
| `outbox` | application outbox helpers |
| `channel_publisher` | channel publishing abstraction |
| `publish_ledger` | publication ledger helpers |
| `vacancy_feedback` | feedback ingestion/application services |
| `logging` | logging setup |
| `llm_usage` | usage accounting |
| `llm_pricing` | model pricing helpers |

## Evaluation, config, operations

| Module | Role |
|---|---|
| `config_resolution` | provenance of resolved config layers |
| `concurrency` | adaptive concurrency resolution |
| `run_budget` | async call/run budget primitives |
| `dataset_hashing` | dataset hash helpers for eval/reproducibility |
| `release_gates` | release validation helpers |
| `search_text` | search text normalization |
| `enrichment` | application-level enrichment contracts |

## Top-level classes and protocols

| Module | Classes / protocols |
|---|---|
| `builder` | `_ShotLoadPlan`, `PipelineBuilder` |
| `channel_publisher` | `TransientSendError`, `FatalTargetError`, `CardSender`, `PublishOutcome` |
| `concurrency` | `ConcurrencyPlan` |
| `contracts` | `Source`, `Stage`, `PipelineNode`, `SanitizingNode`, `ProcessingNode`, `TypeChangingNode`, `FanOutStage`, `Sink`, `DeliveryTarget`, `FlushableSink`, `Store`, `StoreConnector`, `AuthProvider`, `BgeMThreeProviderPort`, `LLMProvider`, `CrossEncoderProvider`, `ShotStoreClearError`, `ManagedShotBackend`, `OntologyStore`, `ClassificationResult`, `PluginMetadata`, `ClassifierProvider`, `JobGroupStore`, `JobPersistenceBackend`, `SearchBackend`, `EmbeddingProvider`, `VectorBackend`, `IngestMode`, `ProxyManager`, `BypassStrategy`, `BrowserSessionBypass`, `BrowserSessionProbe`, `BoardMonitor`, `JobScraper`, `Normalizer`, `LanguageDetectorPort`, `TranslatorPort`, `CrossEncoderPort` |
| `delivery` | `SinkDeliveryTarget` |
| `drops` | `RawItemDropped` |
| `enrichment` | `PostAcceptEnrichmentQueue` |
| `identity` | `JobIdentityMatcher` |
| `llm_usage` | `LLMUsageLedger` |
| `ontology_compiler` | `OntologyCompilerPrompts`, `LabeledOntologyShot`, `OntologyCandidateChunk`, `OntologyCompilationResult` |
| `ontology_snapshot` | `OntologyChange`, `OntologySnapshotDiff`, `OntologyItemReference`, `AffectedOntologyItem`, `OntologyAffectedItemReport` |
| `pipeline` | `StatsBase`, `SourceRunStats`, `RunSummary`, `Pipeline` |
| `plugin` | `PluginKind`, `PluginState`, `PluginDescriptor`, `PluginEntry`, `PluginNotFound`, `DuplicatePlugin` |
| `plugin_registry` | `PluginRegistry` |
| `profile_parsing` | `ResumeExtractionPayload` |
| `prompt_builder` | `DecisionProfileBrief`, `DecisionProfileBriefCompiler` |
| `publish_ledger` | `RunStateStore` |
| `registry` | `MonitorEntry`, `SourceRegistryAssessmentHint`, `SiteParserEntry`, `ScraperEntry`, `BypassCapability`, `_NullAuthProvider`, `StorageError` |
| `rejections` | `RawItemRejected` |
| `release_gates` | `ReleaseGateViolation` |
| `resolver` | `DeferredResolverQueue` |
| `run_budget` | `BudgetOutcome`, `BudgetReservation`, `AsyncCallBudget`, `HierarchicalBudget`, `_CircuitState`, `ScopedCircuitBreaker` |
| `scheduler` | `Scheduler` |
| `shadow` | `ShadowDecision`, `ShadowArtifact`, `CanaryScope`, `ShadowChange`, `ShadowReport` |
| `site_parser_manifest` | `SiteParserManifestBrowserConfig`, `SiteParserManifestRuntimeDefaults`, `SiteParserManifestEntry`, `SiteParserManifest` |
| `source_assessment` | `SourceAssessmentContext`, `SourceAssessmentAdapter`, `SourceAssessmentService` |
| `tenant_locks` | `TenantRunAlreadyActiveError`, `TenantRunLockError` |
| `tenant_runner` | `TenantRunner` |
| `tenant_runtime` | `TenantRuntime` |
| `tenant_store` | `TenantStore` |
| `vacancy_feedback` | `RunStateStore` |
| `watermark` | `IncrementalCursor` |
