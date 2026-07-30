---
title: "DecisionPolicy"
description: "Dataclass thresholds для DecisionNode evidence policy."
updated: 2026-07-27
---
# DecisionPolicy

`DecisionPolicy` — immutable dataclass с threshold’ами, которые использует
`DecisionNode`.

## Поля

`job_accept_belief`, `job_accept_certainty` — минимальные belief/certainty для
подтверждения jobness.

`relevance_accept_belief`, `relevance_accept_certainty` — пороги accept по
profile relevance.

`relevance_reject_belief`, `relevance_reject_certainty` — пороги confident
negative relevance.

`hard_constraint_veto_belief`, `hard_constraint_veto_certainty` — veto hard
constraints.

`risk_veto_belief`, `risk_veto_certainty` — veto high risk.

`freshness_min_certainty` — минимальная certainty freshness.

`defer_unknown_critical` — policy flag для deferred path.

## Границы

Это configuration object для `DecisionNode`, а не отдельный node stage.
Registered graph id `decision` соответствует `DecisionNode`.
