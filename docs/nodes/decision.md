---
title: "DecisionNode"
description: "Single policy owner: AssessedJob -> DecisionResult."
updated: 2026-07-27
---
# DecisionNode

`DecisionNode` — единственная чистая policy boundary, которая выбирает routing
lane на основе typed evidence assessments. Producers и scoring nodes не должны
сами менять `JobRecord.routing_decision`.

## Вход и выход

**Вход:** `AssessedJob`.

**Выход:** `DecisionResult` с updated assessed job, routing decision,
`work_state` и reasons.

## Policy thresholds

`DecisionPolicy` задаёт пороги для jobness accept, relevance accept/reject,
hard constraint veto, risk veto и freshness uncertainty.

## Логика решения

Любая degradation reason приводит к `DEFERRED`.

Confirmed hard constraint или high risk дают `REJECT`.

Jobness обязан быть известен и confident positive; confident negative jobness
даёт `REJECT`, uncertainty даёт `DEFERRED`.

Freshness с низкой certainty даёт `DEFERRED`.

Profile relevance без профилей даёт `REVIEW`, а при ожидаемых профилях и
отсутствии evidence — `DEFERRED`.

Cited/strong LLM support может сразу дать `ACCEPT`, если нет cited LLM
contradiction. Иначе решение идёт по aggregated relevance assessments:
confident positive + LLM support = `ACCEPT`, all confident negative =
`REJECT`, mixed/uncertain = `REVIEW`.

## Выходные инварианты

ACCEPT выставляет `routing_decision = ACCEPT` и принудительно
`post_type = JOB_POSTING`, чтобы delivery adapters не чинили cheap extraction
draft.

`DEFERRED` не выставляет terminal routing decision.

## Границы

Это policy logic, а не evidence production. Если нужен новый сигнал, его надо
добавлять как `EvidenceAtom`/assessment upstream, а не как ad hoc условие здесь.
