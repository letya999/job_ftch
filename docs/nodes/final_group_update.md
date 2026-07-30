---
title: "Graph node: final_group_update"
description: "Graph id для финального ACCEPT-only group update."
updated: 2026-07-27
---
# Graph node: `final_group_update`

`final_group_update` — graph id для финального group update на базе
`JobAggregationNode`/group store integration.

Контракт: `JobRecord -> JobRecord`. Узел должен выполняться только после
routing decision и коммитить группировку только для `ACCEPT`.

См. [JobAggregationNode](aggregation.md).
