# pipelines Index

`docs/pipelines/`

Generated index for navigation. Edit source documents, then rerun `uv run python scripts/build_index_docs.py`.

## Files On This Level

- [PipelineBuilder, Pipeline и Graph](builder_and_graph.md) - Как соотносятся PipelineBuilder, Pipeline, YAML graph, GraphPipelineStage и TenantRunner. (Updated: 2026-07-28)
- [Пайплайн фильтрации и отбора вакансий](filtering_pipeline.md) - Короткий overview production filtering path с указателями на recipe и generated graph reference. (Updated: 2026-07-28)
- [Graph control-flow](graph_control_flow.md) - Как GraphExecutor реально исполняет compiled graph: execution modes, effects, evidence, fan-out, deferred/post-accept lanes и terminal boundary. (Updated: 2026-07-30)
- [Generated pipeline graph reference](graphs.md) - Generated reference of configured pipeline graphs and graph hashes. (Updated: 2026-07-26)
- [Relevance funnel](relevance_funnel.md) - Как 30-узловой production-граф отбирает вакансии: слои воронки от дешёвых gate до calibrated terminal decision, drop/defer/route семантика и владеющие ADR. (Updated: 2026-07-30)
