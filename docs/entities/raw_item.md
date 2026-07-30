---
title: "RawItem"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# RawItem

**Слой**: `domain`
**Файл**: `job_ftch/domain/models.py`

## Что это

`RawItem` — минимальный валидный вход пайплайна. Это ещё не вакансия в
нормализованном смысле, а сырой item из конкретного source.

Source обязан отдать либо `RawItem`, либо `QuarantinedRawItem`.

## Основные поля

| Поле | Тип | Назначение |
|---|---|---|
| `schema_version` | `str` | Версия схемы raw payload |
| `stable_id` | `str` | Вычисляемый стабильный идентификатор |
| `source_kind` | `SourceKind` | Тип источника |
| `source_name` | `str` | Имя или alias источника |
| `external_id` | `str \| None` | Внешний ID записи |
| `url` | `AnyHttpUrl \| None` | Ссылка на источник |
| `text` | `str` | Основной текст для дальнейшей обработки |
| `fetched_at` | `datetime` | Когда item был получен |
| `created_at` | `datetime \| None` | Когда публикация была создана в источнике |
| `metadata` | `dict[str, Any]` | Source-specific данные |

## Инварианты

- модель `frozen=True`
- `source_name` и `text` не могут быть пустыми
- должен существовать хотя бы один locator: `external_id` или `url`
- `stable_id` вычисляется из source identity, а не задаётся вручную

## Жизненный цикл

`RawItem` проходит через ранние узлы пайплайна:

- `SanitizeNode`
- `SnapshotFilterNode` при наличии `run_id`
- source context / garbage / post type / hard filter
- `DedupNode`
- optional semantic prefilters

Если item выжил, только тогда он попадает в `ExtractionNode`, где превращается
в `JobDraft`.

## Что не делать

- не использовать `RawItem` как публичный контракт
- не класть секреты в `metadata`
- не рассчитывать на сохранение `RawItem` в job catalog

## Связанные документы

- [Source](source.md)
- [SourceSpec](source_spec.md)
- [JobDraft](job_draft.md)
