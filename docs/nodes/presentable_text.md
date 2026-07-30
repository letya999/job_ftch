---
title: "PresentableTextNode"
description: "LLM/template formatting JobRecord в PresentableJob для Telegram карточки."
updated: 2026-07-27
---
# PresentableTextNode

`PresentableTextNode` добавляет к `JobRecord` поле `presentable` — clean
Markdown/structured карточку вакансии для Telegram/delivery.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `presentable`, если formatting выполнен или применён
template fallback.

Узел пропускает record, если disabled, `presentable` уже есть или routing
decision равен `REJECT`.

## Параметры

`llm` — provider с методом `present(prompt, PresentableJob)`.

`store` — run-state cache по `presentable:<source_record_id>`.

`max_per_run`, `budget`, `enable` — cost controls.

## Логика

Prompt intentionally compact: в него попадают только user-visible vacancy
fields, а не вся metadata с ontology snapshots/BGE vectors/provenance.

Если cache есть и валиден, используется cached `PresentableJob`.

Если budget/max exhausted, provider не поддерживает `present`, LLM упал или
вернул `None`, узел использует deterministic `_template_present()`.

Успешный LLM result сохраняется в run-state cache.

## Границы

Узел не решает relevance и не меняет extracted job semantics. Он только
форматирует already routed record для показа.
