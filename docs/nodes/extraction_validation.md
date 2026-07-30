---
title: "ExtractionValidationNode"
description: "Минимальная post-extraction проверка качества JobDraft."
updated: 2026-07-27
---
# ExtractionValidationNode

`ExtractionValidationNode` проверяет, что `JobDraft` содержит минимально
полезную структуру перед нормализацией в `JobRecord`.

## Вход и выход

**Вход:** `JobDraft`.

**Выход:** `JobDraft`, возможно с дополненным `review_reasons`.

**Drop:** `RawItemDropped`, если описание короче `min_description_chars` или
если одновременно отсутствуют title, company и canonical URL.

## Параметры

`min_description_chars = 30`.

## Логика

Короткое описание считается непригодным для downstream scoring и delivery.

Если нет core identity fields, draft нельзя безопасно превратить в вакансию.

Если отсутствует location, item не дропается: добавляется review reason
`MISSING_LOCATION`.

## Границы

Узел не нормализует поля и не решает relevance. Он только защищает typed
pipeline от слишком пустых drafts.
