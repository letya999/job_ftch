---
title: "Node Catalog"
description: "Полный каталог `job_ftch/nodes/*` по текущему состоянию репозитория."
updated: 2026-07-24
---
# Node Catalog

Полный каталог `job_ftch/nodes/*` по текущему состоянию репозитория.

Статусы:

- `runtime` — участвует в основном runtime path или может включаться через builder
- `optional` — поддерживается, но включается только флагом/сценарием
- `post-accept` — работает после terminal decision
- `historical` — сохранён для legacy/eval/compatibility, но не должен возвращаться в основной runtime graph
- `helper` — вспомогательный или graph-facing узел

| Module | Public node(s) | Status | Role |
|---|---|---|---|
| `sanitize` | `SanitizeNode` | runtime | первый safety/sanitization stage |
| `snapshot_filter` | `SnapshotFilterNode` | optional | freshness gate между запусками |
| `language_context` | `SourceContextNode` | runtime | добавляет source/runtime context |
| `ontology_snapshot` | `OntologySnapshotNode` | optional | фиксирует ontology provenance |
| `candidate_segmentation` | `CandidateSegmentationNode` | optional | явная 1:N candidate boundary |
| `garbage_filter` | `GarbageFilterNode` | runtime | отрицательное IS_JOB evidence по мусору/не-detail страницам |
| `post_type` | `PostTypeClassificationNode` | runtime | ранняя классификация типа поста |
| `hard_filter` | `HardFilterNode` | runtime | hard-constraint evidence без terminal drop |
| `dedup` | `DedupNode` | runtime | duplicate suppression с defer-commit |
| `embedding_prefilter` | `EmbeddingPrefilterNode` | optional | cheap semantic prefilter |
| `bge_embed_node` | `BgeMThreeNode` | optional | BGE-M3 dense/sparse substrate для scoring/evidence |
| `semantic_prefilter` | `SemanticPrefilterNode` | runtime | weak semantic/shot relevance signal before extraction |
| `jobness` | `RawJobnessEvidenceNode`, `JobnessEvidenceProducer`, `JobnessDecisionNode` | optional/historical | raw jobness evidence and legacy decision helper |
| `completeness_gate` | `CompletenessGateNode` | optional | structured-source evidence и extraction-cost hint |
| `extraction` | `ExtractionNode` | runtime | основная `RawItem -> JobDraft` boundary |
| `extraction_validation` | `ExtractionValidationNode` | runtime | validates extracted draft |
| `job_normalization` | `TitleCompanyNormalizationNode`, `LocationWorkModeNormalizationNode`, `CompensationParsingNode`, `SkillNormalizationNode` | runtime | canonicalizes extracted fields |
| `company` | `CompanyCanonicalizer` | helper | company normalization helper |
| `lifecycle` | `JobLifecycleNode` | runtime | lifecycle/closed-expired normalization |
| `language_detection` | `LanguageDetectionNode` | optional | language tagging |
| `match_scoring` | `MultiProfileMatchNode` | runtime | profile-aware match features |
| `lexical_evidence` | `LexicalEvidenceNode` | runtime | lexical evidence producer |
| `risk` | `RiskScoringNode` | runtime | risk evidence producer |
| `quality` | `QualityScoringNode`, `JobValidationNode` | runtime | quality evidence and soft validation |
| `llm_relevance_classification` | `LLMRelevanceClassificationNode`, `LLMRelevanceEvidenceNode` | optional | LLM relevance evidence, never terminal owner |
| `evidence_fanout` | `EvidenceFanOutNode` and producer helpers | runtime | bounded fan-out of evidence producers |
| `decision` | `DecisionNode`, `DecisionPolicy` | runtime/helper | single terminal decision owner |
| `evidence_decision` | `EvidenceDecisionNode` | runtime | wraps fan-out + terminal decision |
| `aggregation` | `JobAggregationNode` | runtime | canonical group commit |
| `post_accept_enrichment` | `PostAcceptEnrichment` | post-accept | queues enrichment tasks after ACCEPT |
| `accept_template_presentation` | `AcceptTemplatePresentationNode` | post-accept | accepted-item presentation templating |
| `embedding` | `EmbeddingNode` | post-accept | embedding side effect after acceptance |
| `presentable_text` | `PresentableTextNode` | post-accept/historical | presentable text generation |
| `translation` | `TranslationNode` | post-accept/historical | translation kept out of terminal path |
| `full_extraction` | `FullExtractionNode` | post-accept | second-pass enrichment для ACCEPT/REVIEW без reroute |
| `reranker` | `RerankerNode` | optional | provider-neutral cross-encoder feature |
| `bge_reranker_node` | `BgeRerankerNode` | optional | native transformers cross-encoder feature |
| `parallel_scoring` | `ParallelScoringNode` | optional | BGE-M3 dense/sparse contrastive score |
| `routing` | `RoutingNode` | historical | legacy terminal routing kept for compatibility |
| `is_job_classifier` | `IsJobNode` | historical | legacy classifier node |
| `relevance` | `AIRoleRelevanceNode` | historical | newer role-relevance node |
| `triage` | `HeuristicTriageNode` | historical | early triage experiment path |
| `triage_extraction` | `TriageExtractionNode` | historical | combined triage/extraction experiment node |
| `decision_aggregator` | `DecisionAggregatorNode` | helper | decision experiment helper |
| `decision_extraction` | `DecisionExtractionNode` | helper | extraction adapter for decision experiments |
| `decision_policy` | module helpers | helper | policy helpers shared by decision flow |
| `need_more_evidence` | `NeedMoreEvidenceNode` | helper | explicit unresolved/defer helper |
| `profile_semantic_evidence` | `ProfileSemanticEvidenceNode` | helper | semantic evidence producer for graph experiments |
| `review_resolution` | `ReviewResolutionNode`, `ReviewResolution` | helper | review resolution contract |
| `source_classifier` | `SourceClassifierNode` | helper | source-level classification helper |
| `uncertainty_router` | `UncertaintyRouterNode` | helper | experiment-time uncertainty routing helper |

## Runtime graph rules

- `SanitizeNode` всегда первый.
- `SnapshotFilterNode`, если включён, всегда второй.
- `EvidenceDecisionNode` — единственная terminal runtime boundary.
- `RoutingNode` — historical terminal writer; основной runtime policy path
  должен идти через `EvidenceDecisionNode`.
- Optional scorer/reranker/enrichment nodes можно включать только через явный
  graph/recipe variant, где понятно, кто потребляет их metadata.

## Per-node docs

Для каждого node-модуля есть отдельный markdown на этом же уровне:
`sanitize.md`, `dedup.md`, `extraction.md`, `evidence_decision.md` и так далее.

`reference.md` остаётся generated contract reference по registered graph node id,
а ручные страницы объясняют назначение и границы node-модулей.
