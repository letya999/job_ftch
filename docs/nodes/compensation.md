---
title: "CompensationParsingNode"
description: "Парсинг compensation из structured metadata или текста description."
updated: 2026-07-27
---
# CompensationParsingNode

`CompensationParsingNode` находится в `job_normalization.py` и заполняет
`JobRecord.compensation`, если extraction не дал готовый `CompensationRange`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord`.

Если `compensation` уже заполнен, узел no-op.

## Логика

Сначала узел пробует structured `metadata.base_salary`: currency, min, max,
period. Min/max приводятся к int, swapped если min > max, period валидируется
через `CompensationPeriod`.

Если structured salary нет, узел ищет salary в description regex’ами для
currency-prefix и currency-suffix форматов.

Поддерживаются `USD`, `EUR`, `GBP`, `RUB`, `RUR`, `KZT`, `$`, `€`, `£`, `₽`.
Суффиксы `k/к` разворачиваются в тысячи.

## Provenance

Structured path добавляет `compensation:structured_metadata`, text path —
`compensation:parsed_from_description`.

## Границы

Узел не конвертирует валюты, не нормализует gross/net/tax и не решает, подходит
ли зарплата пользователю.
