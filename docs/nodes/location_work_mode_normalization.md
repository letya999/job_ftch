---
title: "LocationWorkModeNormalizationNode"
description: "Нормализация location/city/region и вывод work_mode."
updated: 2026-07-27
---
# LocationWorkModeNormalizationNode

`LocationWorkModeNormalizationNode` находится в `job_normalization.py` и
работает с уже созданным `JobRecord`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord`.

## Логика

Если `work_mode` равен `UNKNOWN`, узел ищет remote/hybrid/onsite сигналы в
description, title и location.

Если location фактически содержит только режим работы (`remote`, `hybrid`,
`on-site`, `onsite`), location очищается, чтобы не путать город и формат
работы.

`city` и `region` заполняются из location, если в record они ещё пустые.

Изменения записываются в `provenance.normalization`.

## Границы

Узел не геокодирует, не нормализует страны и не делает policy decision по
допустимости локации. Он только приводит уже извлечённые поля к более
consumable форме.
