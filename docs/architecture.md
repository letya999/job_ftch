---
title: "Архитектура job_ftch"
description: "Полное описание текущей архитектуры: слои, порты, pipeline, graph runtime, ingest stack, bypass и адаптеры."
updated: 2026-07-28
---
# Архитектура job_ftch

`job_ftch` — library-first async pipeline для сбора и обработки вакансий из
Telegram, карьерных сайтов, RSS/API-источников и runtime overlays. Архитектура
держится на строгих границах слоёв, typed contracts, registry-based extension
points и воспроизводимом production graph.

## 1. Границы слоёв

| Слой | Ответственность | Ограничение |
|---|---|---|
| `job_ftch/domain/` | Pydantic-модели, value objects, enums, contract payloads | Только stdlib и `pydantic` |
| `job_ftch/application/` | Порты, registry, builder, pipeline, tenant runtime, source inputs | Без `infrastructure`/`adapters`, кроме composition/runtime exceptions из `scripts/check_module_boundaries.py` |
| `job_ftch/nodes/` | Processing stages и graph-facing node implementations | Без прямых импортов `job_ftch.infrastructure` и `job_ftch.adapters` |
| `job_ftch/sinks/` | Output sinks и sink wrappers | Следует тем же boundary-принципам, что и nodes |
| `job_ftch/infrastructure/` | Реализации портов: sources, stores, LLM, browser, bypass, observability | Может импортировать внешние клиенты |
| `job_ftch/adapters/` | Внешние runtime entrypoints: Telegram bot, MCP, FastAPI, FastStream, Dagster | Использует public application/runtime API |

Разрешённые cross-cutting зависимости вне `domain`: `structlog`,
`opentelemetry-api`, `yaml` и вычислительные библиотеки, если они не затаскивают
infrastructure layer в `nodes`.

Машинная проверка:

```powershell
uv run python scripts/check_module_boundaries.py
```

## 2. Адаптеры, port adapters и plugins

В проекте слово “adapter” используется в нескольких смыслах; смешивать их нельзя.

| Тип | Что делает | Где живёт | Примеры |
|---|---|---|---|
| Port adapter | Реализует application port | `infrastructure/`, `sinks/` | Source, Store, Sink, LLMProvider |
| Runtime adapter | Даёт внешний вход в runtime | `job_ftch/adapters/` | Telegram bot, MCP, FastAPI, FastStream, Dagster |
| Assessment adapter | Оценивает `SourceSpec` до ingest | `application` contract + `infrastructure/source_assessment/` | Telegram/RSS/known/generic assessment |
| Plugin | Способ подключения реализации через registry/entry points | внешний package или local module | source/sink/store/parser/backend plugins |

Plugin — это механизм подключения, а не отдельная архитектурная роль. Один и тот
же объект может быть port adapter по ответственности и plugin-connected по
способу регистрации.

## 3. Основные порты

Source of truth: `job_ftch/application/contracts.py`.

| Протокол | Сигнатура/идея | Назначение |
|---|---|---|
| `Source[T]` | `fetch() -> AsyncIterator[T | QuarantinedRawItem]` | Поставщик входящих элементов |
| `Stage[In, Out]` | `async process(item: In) -> Out | None` | Processing stage или type-changing stage |
| `Sink[T]` | `async emit(item: T) -> None` | Финальный вывод |
| `Store` | run state, dedup, snapshots, runtime records | Состояние pipeline/runtime |
| `AuthProvider` | resolve credentials by source/runtime context | Секреты вне YAML |
| `LLMProvider` | extract/classify/present/generate text | LLM boundary |

Расширяющие порты:

- `JobPersistenceBackend`;
- `SearchBackend`;
- `EmbeddingProvider`;
- `VectorBackend`;
- `BypassStrategy`;
- `ManagedShotBackend`;
- `OntologyStore`;
- `SourceAssessmentAdapter`.

## 4. Семья payload-типов

Каноническая линия данных:

```text
Source -> RawItem -> CandidateSpan[] -> JobDraft -> JobRecord -> JobGroup
```

- `RawItem` существует до extraction boundary.
- `CandidateSpan[]` появляется только в явной one-to-many segmentation path.
- `JobDraft` — структурированный черновик после extraction.
- `JobRecord` — публичный контракт для sinks, persistence и search.
- `JobGroup` — агрегированная вакансия из нескольких source records.

## 5. Путь evidence decision

После нормализации runtime входит в `EvidenceDecisionNode`. Он запускает bounded
`EvidenceFanOutNode`, агрегирует независимые `EvidenceAtom` в typed confidence
assessment и передаёт terminal lane в `DecisionNode`.

Ключевые правила:

- Legacy routing/scoring/LLM judge/reranker/presentation/translation stages не
  владеют terminal decision в production path.
- Unknown critical evidence сохраняется как deferred resolver task.
- Только ACCEPT продолжает canonical group commit и physical delivery outbox.
- REVIEW сохраняется отдельно и не публикуется как ACCEPT.
- Post-accept enrichment ставится в очередь после ACCEPT и не может изменить
  terminal decision.

## 6. Текущий pipeline

Порядок важен и отражает builder/graph contract:

```text
Source.fetch()
  -> SanitizeNode
  -> SnapshotFilterNode                optional, always 2nd when enabled
  -> SourceContextNode
  -> OntologySnapshotNode              optional ontology provenance
  -> CandidateSegmentationNode         optional explicit 1:N boundary
  -> GarbageFilterNode
  -> PostTypeClassificationNode
  -> HardFilterNode
  -> DedupNode                         defer_commit until terminal decision
  -> TfidfLogregRelevancePrefilterNode production graph gate
  -> BgeMThreeNode | EmbeddingPrefilterNode optional variants
  -> SemanticPrefilterNode
  -> RawJobnessEvidenceNode            optional evidence
  -> CompletenessGateNode              optional structured-source annotation
  -> ExtractionNode                    RawItem -> JobDraft
  -> ExtractionValidationNode
  -> TitleCompanyNormalizationNode
  -> SkillNormalizationNode
  -> LocationWorkModeNormalizationNode
  -> CompensationParsingNode
  -> JobLifecycleNode
  -> JobnessEvidenceProducer           optional
  -> LanguageDetectionNode             optional
  -> MultiProfileMatchNode
  -> LexicalEvidenceNode
  -> RiskScoringNode
  -> QualityScoringNode
  -> JobValidationNode
  -> LLMRelevanceClassificationNode    optional evidence, not terminal owner
  -> EvidenceDecisionNode              single terminal boundary
  -> JobAggregationNode                canonical commit for accepted/reviewable records
  -> main sink(s)
```

Production binding:

- runtime: `config/runtime.prod.yaml`;
- graph: `config/pipelines/evidence_v2_compact_prefilter.yaml`;
- graph hash:
  `0d73de0663d220da62e37d9a41159542547d167f9f096088f7ae85ec587e44fb`.

Generated references:

- [pipelines/graphs](pipelines/graphs.md);
- [nodes/reference](nodes/reference.md);
- [nodes catalog](nodes/README.md).

## 7. Побочные каналы

Pipeline имеет отдельные каналы вывода:

- `QuarantinedRawItem` -> quarantine sink;
- `RejectedItem` -> rejected sink;
- review-routed `JobRecord` -> review sink;
- accepted `JobRecord` -> main sink/outbox.

Sinks не должны переписывать весь output file на каждый `emit`.

## 8. Drop, reject и failure

У item есть разные исходы, и их нельзя смешивать:

- `return None` из stage — controlled drop.
- `RawItemDropped` — управляемый drop с reason code.
- `RawItemRejected` — quarantine/security/policy rejection.
- Unexpected exception — isolated item failure; run продолжается, если ошибка
  не случилась на source iterator или final flush уровне.

Эта модель нужна, чтобы metrics, review, retry и release gates не смешивали
“не вакансия”, “опасный payload”, “ошибка инфраструктуры” и “дубликат”.

## 9. Snapshot и dedup

Система использует два разных механизма:

1. `SnapshotFilterNode`
   - freshness/cost optimization;
   - identity включает source locator и content version;
   - неизменившийся item может быть пропущен;
   - изменившийся content replay-ится даже при том же URL/external ID.

2. `DedupNode`
   - exact/near duplicate suppression;
   - использует dedup keys/fingerprints;
   - defer-commit до terminal decision, чтобы не потерять item при later failure.

`SourceAssessmentAdapter` не управляет runtime drop/gate logic. Он только
классифицирует source capability/freshness до ingest.

## 10. Корни композиции

Есть два основных composition path.

### Одноразовый library path

```text
PipelineBuilder + build_nodes() + Pipeline.run()
```

Используется CLI, smoke examples и library consumers.

### Multi-tenant runtime path

```text
TenantRunner + TenantRuntime + TenantStore + GraphPipelineStage/Pipeline
```

`TenantRunner`:

- мержит base и runtime sources;
- запускает source assessment при добавлении runtime sources и lazy для
  config-backed sources;
- применяет source health, pause и probe logic;
- собирает runtime ontology и candidate profiles;
- пишет run history, snapshots, source health, outbox/delivery state.

Ontology details: [ontology/compiler](ontology/compiler.md).

## 11. Source assessment

`SourceAssessmentAdapter` отвечает за pre-ingest оценку источника:

- capability hints;
- freshness evidence;
- known API/monitor/site-parser support;
- estimated bypass/browser need;
- confidence и ingest state.

Он не возвращает `RawItem`, не вызывает LLM и не запускает обычный
`Source.fetch()`. Для career sites assessment может bounded-образом использовать
fingerprints, headers, monitor `can_handle`, parser manifests и малую выборку.

Подробно: [sources/source_assessment](sources/source_assessment.md).

## 12. Career-site ingest stack

Career-site ingest разделён на роли:

- `monitor` — находит вакансии или rich payloads;
- `scraper` — извлекает detail data;
- `site_parser` — domain-specific fast path для сложных сайтов;
- `bypass_strategy` — controlled access escalation;
- `CareerSiteSource` — orchestrates discovery/detail/raw-item emission.

Это не один монолитный scraper. Роли описаны в:

- [sources/ingest_stack](sources/ingest_stack.md);
- [sources/source_stack_reference](sources/source_stack_reference.md);
- [entities/career_site_engines](entities/career_site_engines.md).

## 13. Bypass architecture

`bypass="auto"` управляется signal-driven route graph, а не простой лестницей
tiers. Route состоит из независимых осей:

- transport;
- browser/runtime;
- network/proxy;
- session;
- challenge/CAPTCHA;
- legal/config gates.

Для неизвестного сигнала используется консервативный fallback:

```text
noop -> curl_stealth -> stealth_browser -> nodriver -> camoufox -> cloak
```

Классифицированный сигнал может выбирать capability напрямую:

- TLS/fingerprint signal -> curl impersonation;
- Chromium fingerprint -> Camoufox/browser tier;
- IP/ASN/rate-limit -> proxy на той же transport/browser axis;
- подходящий challenge -> Nodriver/CAPTCHA bounded action.

Proxy — независимая network capability из `config/proxies.yaml` и/или
`JOB_FTCH_PROXY_LIST`, а не “ступень между браузерами”.

Bypass не должен скрываться внутри site parser. Эскалация должна быть driven by
source assessment, failure signals и runtime policy.

Подробно: [sources/bypass_and_escalation](sources/bypass_and_escalation.md).

## 14. Registry и расширяемость

Расширение ядра идёт через registry:

- `@register_source`;
- `@register_source_spec`;
- `@register_sink`;
- `@register_store`;
- `@register_llm`;
- `@register_site_parser`;
- monitor/parser/backend-specific `register_*`.

Builtins и entry points загружаются через `job_ftch/application/registry.py`.
Core dispatch не должен расти через `if/elif`.

## 15. Runtime adapters

Runtime adapters живут под `job_ftch/adapters/`:

- Telegram bot — production-shape deploy;
- MCP server — agent-facing tenant runtime;
- FastAPI bridge;
- FastStream worker;
- Dagster wrapper.

Они используют public API библиотеки и не должны переносить infrastructure
details обратно в `domain`, `application` без разрешённого composition seam или
`nodes`.

## 16. Что не считать реализованным

Наличие `SourceSpec`, ADR или adapter scaffold не означает production-ready
runtime path.

На текущий момент:

- webhook/websocket specs существуют, но push/realtime maturity неоднородна;
- optional adapters имеют разную глубину runtime contract coverage;
- `NotificationSink` и event broadcasting targets остаются архитектурным
  направлением, а не текущим builtin production sink stack.

## 17. Связанные документы

- [Runtime/env](adapters/runtime_and_env.md)
- [Pipeline builder and graph](pipelines/builder_and_graph.md)
- [Filtering pipeline](pipelines/filtering_pipeline.md)
- [Production recipe](recipes/pipeline_recipe.md)
- [Entities](entities/README.md)
- [Node catalog](nodes/README.md)

## 18. Связанные ADR

Evidence/decision:

- [ADR-024](adr/024-canonical-job-contract-and-matching-funnel.md)
- [ADR-055](adr/055-one-to-many-candidate-segmentation-contract.md)
- [ADR-056](adr/056-structured-evidence-jobness-and-extraction.md)
- [ADR-058](adr/058-calibrated-multi-axis-decision-policy.md)
- [ADR-062](adr/062-unified-evidence-and-confidence.md)
- [ADR-063](adr/063-controlled-evidence-fanout-and-deferred-resolution.md)
- [ADR-064](adr/064-post-accept-enrichment-queue.md)

Runtime/delivery/observability:

- [ADR-031](adr/031-run-based-source-snapshot.md)
- [ADR-052](adr/052-immutable-observation-ledger-and-content-versioned-replay.md)
- [ADR-053](adr/053-durable-outbox-and-delivery-idempotency.md)
- [ADR-054](adr/054-terminal-deferred-and-retryable-pipeline-states.md)
- [ADR-069](adr/069-split-operational-and-ml-observability.md)
- [ADR-070](adr/070-mvp-run-delivery-and-graph-promotion-contract.md)
- [ADR-071](adr/071-durable-delivery-and-runtime-degradation.md)

Source/scraping:

- [ADR-046](adr/046-source-assessment-adapter.md)
- [ADR-047](adr/047-adaptive-pipeline-item-concurrency.md)
- [ADR-048](adr/048-proxy-tier-in-adaptive-bypass-chain.md)
- [ADR-050](adr/050-browser-session-bypass-protocol.md)
- [ADR-057](adr/057-hybrid-retrieval-and-cross-encoder-reranking.md)
- [ADR-072](adr/072-career-site-deadline-and-global-work-budgets.md)
- [ADR-074](adr/074-adaptive-route-state-graph.md)
