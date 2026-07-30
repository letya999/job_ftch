---
title: "Graph node: profile_match"
description: "Graph id для MultiProfileMatchNode."
updated: 2026-07-27
---
# Graph node: `profile_match`

`profile_match` — registered graph id для `MultiProfileMatchNode`.

Контракт: `JobRecord -> JobRecord`. Узел считает deterministic profile scores,
заполняет `profile_scores`, `best_profile_id`, `best_score`,
`relevance_score` и hard constraint states, но не является final decision
owner.

См. [MultiProfileMatchNode](match_scoring.md).
