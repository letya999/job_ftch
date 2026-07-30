---
title: "Graph node: source_context"
description: "Graph id для SourceContextNode: язык, source family, trust и observation context."
updated: 2026-07-27
---
# Graph node: `source_context`

`source_context` — зарегистрированный graph id для `SourceContextNode`.

## Контракт

**Вход:** sanitized `RawItem`.

**Выход:** `RawItem` с обновлёнными `metadata` и `source_identity`.

Узел не дропает item, не вызывает внешние сервисы и не принимает relevance
decision.

## Что добавляет

`detected_language` — дешёвая RU/EN/UNKNOWN эвристика по кириллице/латинице.

`source_family`, `observation_kind`, `transport` — нормализованный source
identity для evidence provenance.

`source_trust` — грубый trust score по source kind.

`source_context` — строковый ключ `<source_kind>:<source_name>` для логов и
tracing.

Для Telegram также пишет `has_hashtags`, `has_urls`, `approx_word_count`.

## Реализация

Реализация находится в [language_context.md](language_context.md).
