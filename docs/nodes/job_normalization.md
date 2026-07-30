---
title: "Job normalization nodes"
description: "Title/company, location/work mode, compensation и skills normalization."
updated: 2026-07-27
---
# Job normalization nodes

`job_normalization.py` содержит несколько normalization stages, которые
превращают `JobDraft` в более чистый `JobRecord` и дообогащают уже созданный
record.

## TitleCompanyNormalizationNode

**Вход:** `JobDraft`.

**Выход:** `JobRecord`.

Узел чистит title/company от HTML, prefix’ов вроде `hiring:`/`vacancy:`/`ищем`,
пробует разделить `title at company`, выводит `role_family` и `seniority`
через injected `Normalizer`, затем вызывает `draft_to_record()`.

Что меняет: `title`, `title_normalized`, `company`, `company_canonical`,
`company_name_raw`, `company_name_normalized`, `description`, `role_family`,
`seniority`, `provenance.normalization`.

## LocationWorkModeNormalizationNode

**Вход/выход:** `JobRecord -> JobRecord`.

Если `work_mode` неизвестен, узел выводит его из description/title/location:
remote, hybrid, onsite. Если location на самом деле равен work mode (`remote`,
`hybrid`, `onsite`), location очищается. `city` и `region` заполняются из
location, если ещё пустые.

## CompensationParsingNode

**Вход/выход:** `JobRecord -> JobRecord`.

Если compensation уже есть, узел no-op. Иначе сначала читает structured
`metadata.base_salary`, затем пробует regex по description. Поддерживаются USD,
EUR, GBP, RUB/RUR, KZT и символы валют; `k/к` разворачивается в тысячи.

## SkillNormalizationNode

**Вход/выход:** `JobRecord -> JobRecord`.

Нормализует `skills_explicit` и `skills_inferred` через injected `Normalizer`.
Если изменились skills, добавляет `skills:normalized` в provenance.

## Границы

Эти узлы не принимают relevance decision и не должны вызывать LLM. Их область —
детерминированная нормализация typed fields и provenance.
