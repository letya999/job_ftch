---
title: "PostAcceptEnrichment"
description: "Последовательный post-ACCEPT enrichment и replacement в group store."
updated: 2026-07-27
---
# PostAcceptEnrichment

`PostAcceptEnrichment` запускает синхронный набор post-accept stages для
records, которые уже приняты routing policy. Без injected stages узел остаётся
eval-safe no-op с metadata marker.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** enriched `JobRecord`.

Если `routing_decision` не `ACCEPT`, узел возвращает record без изменений.

## Параметры

`stages` — tuple stages, которые последовательно получают текущий `JobRecord`.
Production обычно inject’ит full extraction и presentation stages.

`group_store` — optional store для замены canonical member после enrichment.

## Логика

Каждый stage обязан вернуть record. Если stage вернул `None`, это runtime
ошибка: accepted record нельзя silently потерять после policy decision.

После stages узел добавляет `metadata.post_accept_enrichment = completed`.

Если задан `group_store`, accepted record обязан иметь `group_id`. Узел
вызывает `replace_member(group_id, current)`, если метод есть; иначе fallback
`merge(group_id, current, merge_confidence=1.0)`.

## Границы

Узел не принимает ACCEPT, а работает только после ACCEPT. Его задача — заменить
каноническую запись enriched версией до того, как результат увидят adapters.
