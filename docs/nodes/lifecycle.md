---
title: "JobLifecycleNode"
description: "Freshness/lifecycle enrichment: open/filled signals и freshness evidence state."
updated: 2026-07-27
---
# JobLifecycleNode

`JobLifecycleNode` помечает вакансию как open/filled, если source payload или
текст явно содержит lifecycle status.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с обновлённым `status`, metadata
`freshness_evidence_state` и provenance, если статус изменился.

## Логика

Узел читает metadata keys `status`, `state`, `job_status`,
`lifecycle_status`, boolean keys `closed`, `is_closed`, `filled`,
`is_filled`, а также текстовые markers вроде `position closed`, `role has been
filled`, `вакансия закрыта`.

`freshness_evidence_state` принимает значения:
`explicit_status`, `observed_at`, `locator_only`, `missing`.

Если status уже равен inferred status или inferred status отсутствует, узел
обновляет только freshness metadata.

## Границы

Lifecycle не равен relevance. Закрытая вакансия может дать freshness veto в
decision policy, но сам узел не должен менять routing decision.
