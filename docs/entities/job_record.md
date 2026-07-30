---
title: "JobRecord"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# JobRecord

**Слой**: `domain`
**Файл**: `job_ftch/domain/models.py`

## Что это

`JobRecord` — финальный канонический контракт вакансии, который покидает
основной pipeline и попадает в sinks, persistence и search.

Это публичный тип, на который должны ориентироваться внешние consumers.

## Происхождение

`JobRecord` строится после extraction, validation, normalization, scoring,
aggregation и routing-related stages. Он наследует более широкий доменный job
контракт и дополняет его runtime-полями.

## Ключевые поля

### Identity

- `job_id`
- `group_id`
- `schema_version`

### Source and content

- `source_record_id`
- `source_kind`
- `source_name`
- `source_url`
- `canonical_url`
- `title`
- `company`
- `location`
- `description`

### Raw/clean mirror fields

- `description_raw`
- `description_clean`
- `company_name_raw`
- `company_name_normalized`

### Matching and routing

- `profile_scores`
- `best_profile_id`
- `best_score`
- `routing_decision`

### Quality and risk

- `hiring_intent`
- `extraction_completeness`
- `risk_score`
- `risk_level`

### Presentation and aggregation

- `presentable`
- `aggregate_source_count`
- `aggregation_confidence`

## Важные свойства модели

- если `best_profile_id` ещё не задан, он может быть выведен из `profile_scores`
- если `risk_level` пустой, он вычисляется из `risk_score`
- если `job_id` пустой, он подставляется из `stable_id`
- `schema_version` — часть публичного контракта

## Что не делать

- не писать наружу `JobDraft` вместо `JobRecord`
- не менять shape публичных полей без осознанной schema evolution strategy
- не путать source-level identity (`source_record_id`, `source_url`) и aggregate identity (`group_id`)

## Где используется

- `Sink[JobRecord]`
- `JobPersistenceBackend`
- search backends
- tenant/runtime views
- Telegram/MCP/API adapters

## Связанные документы

- [JobDraft](job_draft.md)
- [JobGroup](job_group.md)
- [Sink](sink.md)
- [Backend](backend.md)
