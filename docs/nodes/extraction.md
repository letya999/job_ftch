---
title: "ExtractionNode"
description: "RawItem -> JobDraft: structured extraction, fallbacks, budgets и early validation."
updated: 2026-07-27
---
# ExtractionNode

`ExtractionNode` превращает `RawItem` в `JobDraft`. Это первая стадия, где
неструктурированный текст становится typed vacancy payload.

## Вход и выход

**Вход:** `RawItem` или уже готовый `JobDraft`.

**Выход:** `JobDraft`.

**Drop:** `RawItemDropped`, если extraction дал пустое описание, не дал ни
title/company/canonical URL, либо LLM уверенно классифицировал пост как
`candidate_seeking`, `announcement` или `spam` без source/structure override.

Если на вход уже пришёл `JobDraft`, узел возвращает его без повторного
extraction.

## Параметры

`llm` — `LLMProvider` для structured extraction.

`budget` / `max_calls` — ограничение LLM calls. При исчерпании budget узел
создаёт deferred/partial draft вместо падения всего pipeline.

`target_roles` — используются только для scoring `search_relevance`; prompt
явно запрещает извлекать их как job title.

`scope = core | full` — дешёвая core schema или полный extraction payload.

`extraction_mode = llm_or_structured | structured_or_heuristic` — graph param,
который управляет fallback path.

## Логика

Structured fast path включается, если upstream metadata содержит
`extraction_cost_hint = structured`. Тогда поля собираются из metadata и raw
text без LLM call.

Обычный path строит fenced prompt с `UNTRUSTED_SOURCE_TEXT`, вызывает
`llm.extract()` и валидирует результат через `CoreExtractedJobFields` или
`ExtractedJobFields`.

При LLM/schema failure узел деградирует в пустые extracted fields и собирает
draft через fallback-поля: title/company/location/url/work mode из metadata,
text и source fields.

Post type после extraction важнее fast preclassifier. Если LLM видит
candidate/announcement/spam, узел дропает item, но source-confirmed career
detail или сильная vacancy structure могут override’ить ошибочный non-job type.

## Что формирует

`JobDraft` получает raw/source ids, title/company/description/canonical URL,
location/work mode, post type, language, relevance scores, role/seniority,
skills, compensation, provenance и review reasons.

## Границы

`ExtractionNode` не принимает финальное profile match решение. Он только
строит typed job draft и отбрасывает случаи, где сама сущность вакансии не
собралась или оказалась явно не вакансией.
