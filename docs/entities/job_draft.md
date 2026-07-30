---
title: "JobDraft"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# JobDraft

**Слой**: `domain`
**Файл**: `job_ftch/domain/models.py`

## Что это

`JobDraft` — промежуточный структурированный объект после extraction boundary.
Это уже не сырой текст, но ещё не финальный публичный контракт.

`JobDraft` создаётся в `ExtractionNode` и затем проходит через валидацию,
нормализацию и enrichment stages.

## Что в нём важно

`JobDraft` уже содержит:

- source identity: `source_record_id`, `source_kind`, `source_name`, `source_url`
- time fields: `fetched_at`, `posted_at`
- extraction provenance
- raw extracted fields: `title_raw`, `company_name_raw`, `description_raw`
- normalized enums и частично структурированные поля
- review reasons для borderline extraction

## Группы полей

### Identity and provenance

- `draft_id`
- `raw_item_id`
- `source_record_id`
- `source_kind`
- `source_name`
- `source_url`
- `canonical_url`
- `fetched_at`
- `posted_at`
- `provenance`

### Core extracted content

- `title_raw`
- `company_name_raw`
- `description_raw`
- `location_raw`
- `work_mode`
- `compensation`
- `post_type`
- `ai_relevance`
- `hiring_intent`

### Role and metadata hints

- `role_family`
- `role_track`
- `role_specialization`
- `seniority`
- `employment_type`
- `domain`
- `industry`

### Lists and signals

- `responsibilities`
- `requirements_must`
- `requirements_nice`
- `skills_explicit`
- `skills_inferred`
- `tools_stack`
- `benefits`
- `culture_signals`
- `risk_signals`

### Plan B extension fields

В модели уже есть дополнительные поля для richer extraction:

- `years_experience`
- `education`
- `relocation`
- `visa_support`
- `domain_knowledge`
- `soft_skills`
- `certifications`
- `leadership_level`
- `ic_or_manager`
- `company_type`
- `team_size_hint`
- `remote_restrictions`

## Инварианты

- `description_raw` обязателен
- `raw_item_id` и `source_name` не могут быть пустыми
- tuple-поля нормализуются и дедуплицируются
- `draft_id` вычисляется автоматически

## Что дальше

После `ExtractionNode` тип остаётся `JobDraft` до тех пор, пока normalization
и последующие nodes не доведут данные до `JobRecord`.

`JobDraft` не должен уходить в публичные sinks или долгосрочное job storage.

## Связанные документы

- [RawItem](raw_item.md)
- [JobRecord](job_record.md)
- [LLMProvider](llm_provider.md)
