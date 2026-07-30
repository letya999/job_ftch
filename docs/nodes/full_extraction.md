---
title: "FullExtractionNode"
description: "Second-pass enrichment для ACCEPT/REVIEW без reroute."
updated: 2026-07-27
---
# FullExtractionNode

`FullExtractionNode` дообогащает `JobRecord`, который уже прошёл routing policy
как `ACCEPT` или `REVIEW`. Это second-pass extraction после решения, а не
повторный gate.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с более полными fields, либо исходный/deferred record.

Узел пропускает `REJECT` и records с `metadata.work_state = deferred`.

## Параметры

`llm` — provider для внутреннего `ExtractionNode(scope="full")`.

`budget` / `max_calls` — лимитируют second-pass LLM calls.

`target_roles`, `capture_payloads` передаются во внутренний extractor.

## Логика

Узел восстанавливает `RawItem` из `JobRecord`: raw/source ids, URL, fetched/posted
timestamps, original posting text из metadata или description.

Если budget исчерпан, добавляет `full_extraction_deferred` и metadata reason.

Если extraction degraded, не ломает routing: добавляет review reason
`full_extraction_deferred`.

Успешный full extraction обновляет user-visible поля: title, company, location,
language, work mode, seniority, employment type, role/domain/industry,
compensation, responsibilities, requirements, skills, tools, benefits,
culture, education, relocation, visa support и прочие enrichment fields.

## Границы

Узел не имеет права переводить ACCEPT в REJECT или менять routing decision.
Его задача — улучшить карточку/экспорт после policy decision.
