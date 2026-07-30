---
title: "Graph control-flow"
description: "Как GraphExecutor реально исполняет compiled graph: execution modes, effects, evidence, fan-out, deferred/post-accept lanes и terminal boundary."
updated: 2026-07-30
---
# Graph control-flow

Этот документ описывает механику исполнения одного item внутри
`GraphExecutor` (`job_ftch/application/graph/executor.py`,
контракты — `job_ftch/application/graph/contracts.py`). Обзорные документы
([builder_and_graph.md](builder_and_graph.md), [filtering_pipeline.md](filtering_pipeline.md))
фиксируют, кто за что отвечает; здесь — как узлы реально запускаются и как item
доходит до accept/reject/review/deferred. Порядок узлов конкретного графа берётся
из сгенерированного [graphs.md](graphs.md), не отсюда.

## Compiled graph и проход

`CompiledGraph` содержит `spec.nodes`, `graph_hash` и плоский `execution_order`.
Исполнение — линейный проход по `execution_order` с ветвлением по режиму узла.
`run_many(item)` возвращает список `ExecutionReport` (fan-out может дать больше
одного), `run(item)` — ровно один. Каждый report несёт `item`, `status`
(`ACCEPT`/`REJECT`/`REVIEW`/`DEFERRED`), `evidence` (bundle), `diagnostics` и
`node_events` (полная per-node observability для replay).

## Execution modes (lanes)

`ExecutionMode` определяет, как узел встраивается в проход:

- **SEQUENTIAL** — обычный шаг: `await node.process(item)`, результат становится
  новым payload.
- **PARALLEL** — соседние parallel-узлы собираются в группу и запускаются
  `asyncio.gather` над **snapshot** item (deepcopy); результаты того же типа
  мёржатся в payload (`_merge_payload` объединяет `metadata`, не теряя evidence
  из-за порядка завершения).
- **BACKGROUND** — узел стартует на deepcopy-snapshot как task и **не** блокирует
  проход; все background-таски джойнятся перед следующим SEQUENTIAL-узлом
  (`_join`). Упавший background даёт `unknown`, а не фейл item.
- **DEFERRED** — item помечается `DEFERRED` и проход немедленно возвращается
  (retryable-состояние, ADR-054).
- **POST_ACCEPT** — узел исполняется только если `status == ACCEPT`; иначе
  `skipped_post_accept`. Так enrichment не трогает отклонённые item.

## Effects (что узел делает с потоком)

`EffectMode` объявляет полномочия узла над control flow:

- **OBSERVE** — только аннотация/evidence, не влияет на решение.
- **GATE** — если узел вернул `None` и он не `shadow`, item получает `REJECT`, и
  проход возвращается сразу (`gate_returned_none`). Shadow-gate вместо дропа даёт
  `would_drop` (наблюдение без действия). Явный дроп узел может дать и через
  `RawItemDropped`/`RawItemRejected`.
- **TERMINAL_DECISION** — единственная граница, ставящая финальный `status`.
  Перед вызовом узлу прикрепляется накопленный `EvidenceBundle`
  (`_attach_evidence` кладёт immutable evidence в `metadata._graph_evidence`, не
  копируя текст/векторы). `status` берётся из `routing_decision`/`work_state`
  результата (`_terminal_status`). Typed decision-инпуты сохраняются в артефакт
  `typed_evidence` для offline-калибровки.
- **STATEFUL_CHECKPOINT** / **SIDE_EFFECT** — узлы с внешним эффектом (запись
  состояния, side-каналы), не меняющие relevance.

`AuthorityMode` (observe/shadow/gate/terminal) — декларация того, может ли узел
влиять на поток; shadow-узлы всегда наблюдательны, что позволяет катить кандидата
в graph и мерить его, не отдавая ему власть над решением.

## Условный запуск (run_if)

Узел может нести `run_if` + `on_unknown_condition`. Условие вычисляется по
текущему item (`evaluate_condition`); `TRUE` -> запуск, `FALSE` -> `skipped_condition`,
`UNKNOWN` -> запуск только если `on_unknown_condition == "run"`. Это позволяет
графу держать узлы, релевантные лишь части item, без ветвления самого графа.

## Evidence: patch -> bundle -> terminal

Non-terminal узлы не «голосуют» напрямую. Они эмитят `EvidencePatch`
(`claim`, `producer`, `independence_group`, `recommendation`, `reliability`,
`features`, `reason`) — по одному или как `EvidenceBundle` / через
`evidence_patches`. Executor мёржит их в `report.evidence` (дедуп по
`claim/producer/independence_group/reliability`, ADR-062/063). Терминальный узел
получает весь bundle и применяет калиброванную decision policy
(`metadata.decision_policy`, режимы `weighted`/`claims`, ADR-058), возвращая
ACCEPT/REJECT/REVIEW/DEFERRED. Так «кто нашёл сигнал» и «кто принимает решение»
разделены: producers независимы, решение — одно.

## Fan-out (one-to-many)

Если узел помечен `is_fan_out_stage` и вернул кортеж кандидатов, executor для
каждого материализует item (`materialize_raw_item`) и рекурсивно доигрывает
остаток графа (`_run_from(child, index+1)`). Так один источниковый item может
дать несколько канонических кандидатов (segmentation, ADR-055/063), каждый со
своим terminal-исходом.

## Deferred dedup claims

`run_many` ведёт список пройденных item и в конце сеттлит их dedup-claims:
`commit_claim` при успехе, `release_claim` при исключении (mirror логики
`Pipeline`). Это удерживает claim-lifecycle корректным даже для декларативного
graph-исполнения и не даёт «повиснуть» claim при падении прохода.

## Ошибки, таймауты, деградация

- `RawItemDropped` / `RawItemRejected` -> `REJECT` с явной причиной и
  diagnostic-метаданными (включая prefilter score/threshold).
- таймаут узла (`timeout_ms`, дефолт 30s) -> `timeout`, узел возвращает `None`
  (для gate это дроп, для observe — потеря сигнала).
- `on_error == "unknown"` или parallel/background узел -> исключение становится
  `unknown` (evidence «неизвестен»), проход продолжается; иначе исключение
  пробрасывается и обрывает item.

Каждый узел пишет `node_events[id]` с outcome/reason/timing/evidence — это основа
replay и operational observability (ADR-069).

## Где это стыкуется с Pipeline

`GraphPipelineStage` оборачивает compiled graph в обычный `Stage`, так что
`Pipeline` гоняет весь граф как один processing-stage и остаётся lifecycle-
оркестратором (source iterator, sinks, quarantine, snapshot, RunSummary).
`TenantRunner` перед сборкой executor валидирует `graph_hash` против
`pipeline_graph_expected_hash` из runtime config (ADR-051). Подробнее —
[builder_and_graph.md](builder_and_graph.md).

## Где смотреть код

- `job_ftch/application/graph/executor.py`
- `job_ftch/application/graph/contracts.py`
- `job_ftch/application/graph/compiler.py`, `conditions.py`, `policy.py`
- `job_ftch/application/graph/pipeline_stage.py`

## Связанные документы

- [Relevance funnel](relevance_funnel.md)
- [PipelineBuilder, Pipeline и Graph](builder_and_graph.md)
- [Пайплайн фильтрации и отбора вакансий](filtering_pipeline.md)
- [Generated pipeline graph reference](graphs.md)
- [Node Catalog](../nodes/README.md)
