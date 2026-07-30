---
title: "SourceContextNode"
description: "RawItem enrichment: язык, source family, trust и Telegram parsing hints."
updated: 2026-07-27
---
# SourceContextNode

`SourceContextNode` добавляет к `RawItem` минимальный runtime/source context,
который нужен ранним evidence nodes и extraction path.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem` с metadata-полями `detected_language`, `source_family`,
`observation_kind`, `transport`, `source_trust`, `source_context`; для Telegram
также `has_hashtags`, `has_urls`, `approx_word_count`.

## Логика

Язык определяется локально по счётчикам кириллицы и латиницы. Это дешёвая
эвристика, а не полноценный language detector.

Source family и trust выводятся из `SourceKind`: career site получает
`CAREER_WEB` и высокий trust, Telegram channel/group/comment получают
`TELEGRAM` с разным trust, debug получает `FIXTURE`.

`source_identity` обновляется так, чтобы downstream `EvidenceAtom` мог
фиксировать family, observation kind и transport.

## Границы

Это context enrichment, а не `SourceAssessmentAdapter`. Capabilities источника,
bypass strategy, crawler/parser пригодность и эскалации описываются в source
assessment/bypass stack.

Узел также не заменяет downstream `LanguageDetectionNode`, который работает уже
на `JobRecord` и может использовать внешний detector port.
