---
title: "EvidenceDecisionNode"
description: "Compatibility bridge JobRecord -> evidence fan-out -> DecisionNode -> JobRecord."
updated: 2026-07-27
---
# EvidenceDecisionNode

`EvidenceDecisionNode` — bridge для legacy `JobRecord` pipeline, внутри
которого запускаются `EvidenceFanOutNode`, `DecisionNode` и deferred handling.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с обновлёнными review reasons, metadata evidence и
`work_state`.

## Зависимости

Если dependencies не переданы, узел сам создаёт `EvidenceFanOutNode` с
parameters из `settings.evidence_policy_path` и `DecisionNode`.

Также внутри используется `NeedMoreEvidenceNode` для deferred outcome.

## Логика

1. Превращает `JobRecord` в `AssessedJob` через fan-out.
2. Получает `DecisionResult` через `DecisionNode`.
3. Если decision deferred, прогоняет assessed job через `NeedMoreEvidenceNode`.
4. Пишет serialized `evidence_atoms`, `evidence_assessments`,
   `evidence_policy_version`, `decision_reasons`, `work_state` и optional
   `deferred_reason` в metadata.
5. Добавляет decision reasons в `review_reasons`.

## Границы

Это не второй orchestrator и не отдельная decision policy. Это адаптерный узел,
который встраивает новый typed evidence boundary в существующий
`JobRecord -> JobRecord` graph.
