---
title: "Пайплайн фильтрации и отбора вакансий"
description: "Короткий overview production filtering path с указателями на recipe и generated graph reference."
updated: 2026-07-28
---
# Пайплайн фильтрации и отбора вакансий

Этот документ больше не дублирует production recipe и generated graph
reference. Он нужен как короткий обзор того, где искать правду по filtering
path.

## Что является source of truth

- production recipe:
  `config/recipes/production_pipeline_recipe.yaml`
- production runtime binding:
  `config/runtime.prod.yaml`
- человекочитаемый runbook:
  [Рецепт production-пайплайна](../recipes/pipeline_recipe.md)
- generated graph inventory:
  [Generated pipeline graph reference](graphs.md)
- runtime composition:
  `job_ftch/application/builder.py`
- multi-tenant graph execution:
  `job_ftch/application/tenant_runner.py`
  и `job_ftch/application/graph/pipeline_stage.py`

## Короткая карта production path

По состоянию на 2026-07-28 production runtime binding в
`config/runtime.prod.yaml` указывает на
`config/pipelines/evidence_v2_compact_prefilter.yaml` с graph hash
`0d73de0663d220da62e37d9a41159542547d167f9f096088f7ae85ec587e44fb`.

Имя compiled graph: `precision_first_v2_compact_prefilter`. Terminal decision
boundary остаётся один: `EvidenceDecisionNode`.

Важно: `metadata.production_default` внутри отдельных graph manifests является
описательным флагом manifest family. Активный production graph выбирается не по
нему, а через `pipeline_graph_path` / `pipeline_graph_expected_hash` в runtime
config.

Верхнеуровневый ход item:

```text
Source.fetch()
  -> sanitize
  -> source_context
  -> ontology / segmentation
  -> garbage / post_type / hard_constraints
  -> dedup
  -> tfidf_logreg_prefilter
  -> semantic
  -> raw_jobness / completeness
  -> extraction / normalization / lifecycle
  -> profile_match / lexical / risk / quality / validation
  -> relevance_judge
  -> decision
  -> aggregation
  -> post_accept enrichment
```

## Главные инварианты

- `SanitizeNode` всегда первый runtime stage.
- `DedupNode` стоит до LLM relevance path.
- `tfidf_logreg_prefilter` — единственный trainable pre-LLM drop gate в текущем recipe.
- `EvidenceDecisionNode` — единственная terminal runtime boundary.
- post-accept enrichment не имеет права менять terminal decision.
- generated `docs/pipelines/graphs.md` должен совпадать с
  `config/pipelines/*.yaml`; при изменении graph manifests его нужно
  регенерировать через `uv run python scripts/build_graph_reference.py`.

## Что смотреть по темам

- graph hashes, execution order и список graph manifests:
  [Generated pipeline graph reference](graphs.md)
- состав production recipe, pinned graph hash, champion metrics, live snapshot:
  [Рецепт production-пайплайна](../recipes/pipeline_recipe.md)
- builder / graph / Pipeline / TenantRunner:
  [PipelineBuilder, Pipeline и Graph](builder_and_graph.md)
- механика исполнения графа (lanes, effects, evidence, terminal boundary):
  [Graph control-flow](graph_control_flow.md)
- слои relevance-воронки и drop/defer/route семантика:
  [Relevance funnel](relevance_funnel.md)
- узлы и их статусы:
  [Node Catalog](../nodes/README.md)
- TF-IDF prefilter:
  [TF-IDF + Logistic Regression Relevance Prefilter](../nodes/relevance_prefilter.md)
- ontology-backed relevance context:
  [Ontology compiler and runtime projection](../ontology/compiler.md)
