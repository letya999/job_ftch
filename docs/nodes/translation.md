---
title: "TranslationNode"
description: "On-the-fly перевод title/description JobRecord в целевой язык."
updated: 2026-07-27
---
# TranslationNode

`TranslationNode` переводит `JobRecord.title` и `JobRecord.description` в
target language, если upstream language detection показывает другой
поддерживаемый язык.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с переведёнными `title` и `description`.

Если язык неизвестен, уже равен target language или пара языков не
поддерживается translator’ом, узел no-op.

## Параметры

`translator: TranslatorPort` — должен поддерживать `supports()` и
`translate()`.

`target_language = ru`.

## Metadata

Перед переводом узел сохраняет `original_title`, `original_description`,
`translation_source_lang`, `translation_target_lang`.

## Границы

Узел не переводит metadata/evidence/provenance и не принимает delivery policy.
Он меняет только presentation-facing текстовые поля record.
