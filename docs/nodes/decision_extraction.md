---
title: "DecisionExtractionNode"
description: "Combined LLM stage: extraction и relevance decision в одном запросе."
updated: 2026-07-27
---
# DecisionExtractionNode

`DecisionExtractionNode` — subclass `ExtractionNode`, который в одном LLM
запросе извлекает vacancy fields и получает profile relevance decision. Он
сохраняет поведение `ExtractionNode.process()` для fallback/validation/draft
construction и заменяет только `_extract_fields()`.

## Вход и выход

**Вход:** `RawItem` или `JobDraft`.

**Выход:** `JobDraft`; дополнительно в metadata draft добавляется
`_llm_relevance`, если combined request успешно вернул decision.

## Зависимости

`llm` — provider structured extraction.

`store` — run-state cache для combined response.

`catalog` — profile catalog; если profiles нет, узел откатывается к обычному
`ExtractionNode`.

`relevance_prompts` — profile-specific decision brief.

## Логика

Узел строит prompt из profile description, anti preferences, positive/negative
examples, optional decision rules и fenced source text. `prompt_mode` может
сократить prompt, а `brief_max_chars` ограничивает profile brief.

Response schema расширяет extraction schema полями `decision`, `confidence`,
`reasoning`, `matched_positive_aspects`, `mismatched_aspects`.

Результат кешируется по stable id, source digest, profile id, prompt mode,
brief limit и digest профиля/examples/rules.

После `super().process()` узел переносит decision в metadata `_llm_relevance`
в формате, который downstream compatibility routing умеет читать.

## Границы

Это compact альтернативный path для “extraction + LLM relevance”, а не новый
decision policy owner. Финальная маршрутизация всё равно должна соблюдать
graph recipe и routing/decision stage semantics.
