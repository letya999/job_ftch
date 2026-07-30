---
title: "ProfileCatalog"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# ProfileCatalog

**Слой**: `domain`
**Файл**: `job_ftch/domain/profile.py`

## Что это

`ProfileCatalog` — плоский каталог `SearchProfile`, который передаётся в nodes
и scorer paths.

## Текущая модель

| Поле | Тип |
|---|---|
| `catalog_name` | `str` |
| `profiles` | `tuple[SearchProfile, ...]` |

Каталог не хранит пользователя целиком; он хранит именно те search-профили,
которые должны участвовать в матчинге.

## Где используется

- `HardFilterNode`
- `SemanticPrefilterNode`
- `MultiProfileMatchNode`
- prompt/relevance building logic
- runtime ontology merge path

## Практический смысл

`ProfileCatalog` — это удобный read model для pipeline, а не пользовательская
DTO-модель для ввода.

## Связанные документы

- [CandidateProfile](candidate_profile.md)
- [JobDraft](job_draft.md)
- [JobRecord](job_record.md)
