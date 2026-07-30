---
title: "CandidateProfile"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# CandidateProfile

**Слой**: `domain`
**Файл**: `job_ftch/domain/candidate.py`

## Что это

`CandidateProfile` — профиль одного кандидата, который содержит identity,
optional resume snapshot и один или несколько `SearchProfile`.

Это главный пользовательский input для relevance и matching logic.

## Текущая shape

```text
CandidateProfile
  - identity
  - resume | None
  - search_profiles: tuple[SearchProfile, ...]
```

Инвариант: `search_profiles` не может быть пустым.

## Почему здесь несколько SearchProfile

Один кандидат может одновременно искать несколько типов ролей:

- backend
- ML / LLM
- product / analytics

Поэтому matching идёт не по одному монолитному профилю, а по набору
поисковых профилей.

## Где используется

- `SemanticPrefilterNode`
- `MultiProfileMatchNode`
- runtime profile overlays
- prompt building и managed shots flows

## Важное соседнее понятие

`ManagedCandidateProfile` — это runtime-managed оболочка вокруг профиля,
которая добавляет operational metadata и используется в tenant/runtime flows.

## Связанные документы

- [ProfileCatalog](profile_catalog.md)
- [JobRecord](job_record.md)
- [TenantConfig](tenant_config.md)
