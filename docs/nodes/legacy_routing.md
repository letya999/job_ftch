---
title: "Graph node: legacy_routing"
description: "Graph id для historical RoutingNode."
updated: 2026-07-27
---
# Graph node: `legacy_routing`

`legacy_routing` — graph id для `RoutingNode`.

Статус: historical/compatibility. Используется для baseline/legacy presets, где
routing decision пишется напрямую в `JobRecord`.

Контракт: `JobRecord -> JobRecord`; owner текущего production evidence policy —
`EvidenceDecisionNode`/`DecisionNode`, не этот graph id.

См. [RoutingNode](routing.md).
