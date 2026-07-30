---
title: "IsJobNode"
description: "Prototype keyword classifier для job-like text."
updated: 2026-07-27
---
# IsJobNode

`IsJobNode` — prototype classifier, который keyword-эвристикой помечает,
похож ли `JobRecord` на вакансию.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `metadata.is_job_prototype`.

## Логика

Текст строится из title и description. Regex ищет простые markers:
`вакансия`, `требуется`, `ищем`, `приглашаем`, `vacancy`, `hiring`,
`we are looking for`, `job`.

## Границы

Это prototype/diagnostic node. Для production jobness используется typed
evidence path из `jobness.py` и `EvidenceDecisionNode`.
