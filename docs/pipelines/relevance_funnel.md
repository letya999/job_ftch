---
title: "Relevance funnel"
description: "Как 30-узловой production-граф отбирает вакансии: слои воронки от дешёвых gate до calibrated terminal decision, drop/defer/route семантика и владеющие ADR."
updated: 2026-07-30
---
# Relevance funnel

Этот документ описывает production relevance-воронку как последовательность
слоёв: что каждый слой отбрасывает, что аннотирует и где принимается финальное
решение. Механику исполнения см. в [graph_control_flow.md](graph_control_flow.md);
per-node детали — в [Node Catalog](../nodes/README.md); точный порядок и hash —
в сгенерированном [graphs.md](graphs.md).

## Текущий production-граф

Runtime (`config/runtime.prod.yaml` и `config/runtime.dev.yaml`) пинит
`config/pipelines/evidence_v2_compact_prefilter.yaml` — compiled graph
`precision_first_v2_compact_prefilter`, 30 узлов, hash
`0d73de0663d2...`. `TenantRunner` валидирует hash перед сборкой executor.

Порядок исполнения (из graphs.md):

```text
sanitize -> source_context -> ontology -> segmentation
 -> garbage -> post_type -> hard_constraints        # дешёвые gate
 -> dedup
 -> tfidf_logreg_prefilter                          # trainable pre-LLM gate
 -> semantic
 -> raw_jobness -> completeness
 -> extraction -> extraction_validation -> normalization
 -> skills -> location -> compensation -> lifecycle
 -> jobness
 -> profile_match -> lexical -> risk -> quality -> validation
 -> evidence
 -> relevance_judge                                 # LLM
 -> decision                                        # единственный terminal
 -> aggregation
 -> enrichment                                      # post-accept
```

Принцип воронки: чем ниже слой, тем дороже вызов, поэтому дешёвые
детерминированные gate стоят раньше, а LLM-judge и calibrated decision — в самом
конце, после того как evidence уже собрана.

## Слои

### 1. Intake и нормализация контекста
`sanitize` (всегда первый runtime-узел), `source_context`, `ontology`,
`segmentation`. Приводят сырой item к каноническому виду, навешивают
source/ontology-контекст и, при необходимости, разбивают один входной item на
несколько кандидатов (fan-out, ADR-055/063). Это ещё не фильтрация по профилю.

### 2. Дешёвые gate
`garbage` (ADR-066 heuristic triage), `post_type`, `hard_constraints`. Убирают
заведомый мусор и структурно неподходящие посты (не-вакансия, нарушение жёстких
ограничений) до любых дорогих операций. Дроп здесь — `REJECT` через gate-эффект
(см. GATE в [graph_control_flow.md](graph_control_flow.md)).

### 3. Dedup
`dedup` стоит **до** LLM-пути и защищает воронку от повторной обработки уже
виденного content (identity/fingerprint, ADR-005). Claim-lifecycle
(commit/release) удерживается executor'ом/Pipeline.

### 4. Trainable prefilter
`tfidf_logreg_prefilter` — единственный обучаемый pre-LLM drop-gate в текущем
recipe (ADR-078). Эффект `gate`, `threshold=0.20`, модель
`fixtures/prefilter/tfidf_logreg_v1.json`. Он дёшево срезает явные негативы перед
дорогим LLM-judge, оставляя ему управляемый объём. Score/threshold/decision
пишутся в metadata и попадают в diagnostics при дропе.

### 5. Semantic prefilter
`semantic` — semantic/embedding-слой relevance-контекста (ADR-057 hybrid
retrieval). Уточняет relevance-сигнал перед jobness/scoring. Embeddings/BGE в
текущем графе находятся **вне** terminal decision path (см. комментарий в
runtime.prod.yaml) — они дают evidence, а не решают.

### 6. Jobness evidence
`raw_jobness`, `completeness`, позже `jobness` — структурированная оценка того,
что документ действительно является вакансией и достаточно полон (ADR-056/067).
Это evidence-producers, а не terminal.

### 7. Извлечение и нормализация
`extraction` -> `extraction_validation` -> `normalization` -> `skills` ->
`location` -> `compensation` -> `lifecycle`. Достают и нормализуют
поля вакансии (роль, навыки, локация/режим работы, компенсация, lifecycle-статус)
для последующего scoring и presentation (ADR-024/029).

### 8. Scoring evidence
`profile_match`, `lexical`, `risk`, `quality`, `validation` — по-осевые
producers relevance/quality evidence относительно профиля кандидата
(ADR-041 three-layer, ADR-044 parallel scoring, ADR-058 axes). Они эмитят
`EvidencePatch`, но не принимают решение.

### 9. Evidence collect и LLM judge
`evidence` собирает накопленные сигналы в единый bundle (ADR-062/063).
`relevance_judge` — LLM-слой, ограниченный бюджетами
(`max_ambiguity_resolutions=40`, `max_precision_confirmations=20`): LLM
вызывается не на каждый item, а только чтобы разрешить неоднозначность или
подтвердить precision, что держит стоимость под контролем.

### 10. Terminal decision
`decision` (`EvidenceDecisionNode`) — **единственная** terminal boundary
(эффект `terminal_decision`). Она получает immutable evidence bundle и применяет
calibrated multi-axis policy (ADR-058, binary routing ADR-042), выставляя
`ACCEPT` / `REJECT` / `REVIEW` / `DEFERRED`. Никакой другой узел не меняет
финальный статус.

### 11. Aggregation и post-accept
`aggregation` — кросс-источниковая группировка канонических вакансий
(ADR-016/059). `enrichment` — post-accept слой (`execution=post_accept`),
исполняется только для `ACCEPT` и **не имеет права** менять terminal decision
(ADR-064).

## Инварианты воронки

- `sanitize` первый; `dedup` до LLM-пути; `tfidf_logreg_prefilter` —
  единственный trainable pre-LLM drop-gate; `decision` — единственная terminal
  boundary; post-accept не меняет решение.
- Дешёвое раньше дорогого: детерминированные gate -> prefilter -> LLM -> decision.
- Producers независимы и наблюдательны; власть над потоком имеют только gate и
  terminal (см. AuthorityMode в [graph_control_flow.md](graph_control_flow.md)).
- `graphs.md` и recipe — source of truth по узлам/hash; при изменении manifests
  их регенерируют (`scripts/build_graph_reference.py`).

## Drop / defer / route / accept

| Исход | Где возникает | Смысл |
|---|---|---|
| REJECT (drop) | любой gate вернул None / RawItemDropped; terminal REJECT | item не проходит |
| DEFERRED | deferred-узел или terminal work_state=deferred | retryable, повтор позже (ADR-054) |
| REVIEW | terminal вернул review-решение | ручная/отложенная проверка |
| ACCEPT | terminal ACCEPT | item идёт в aggregation + post-accept + sinks |

## Где смотреть

- recipe и champion-метрики: [Рецепт production-пайплайна](../recipes/pipeline_recipe.md)
- порядок узлов и hash: [graphs.md](graphs.md)
- узлы по отдельности: [Node Catalog](../nodes/README.md),
  в частности [relevance_prefilter.md](../nodes/relevance_prefilter.md),
  [evidence_decision.md](../nodes/evidence_decision.md),
  [decision_policy.md](../nodes/decision_policy.md)
- ADR-041, ADR-042, ADR-044, ADR-056, ADR-057, ADR-058, ADR-062, ADR-063, ADR-078

## Связанные документы

- [Graph control-flow](graph_control_flow.md)
- [PipelineBuilder, Pipeline и Graph](builder_and_graph.md)
- [Пайплайн фильтрации и отбора вакансий](filtering_pipeline.md)
- [Ontology compiler and runtime projection](../ontology/compiler.md)
