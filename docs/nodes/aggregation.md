---
title: "JobAggregationNode"
description: "ACCEPT-only cross-source grouping и JobGroupStore side effect."
updated: 2026-07-27
---
# JobAggregationNode

`JobAggregationNode` группирует accepted vacancies across sources в canonical
`JobGroup`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** тот же `JobRecord`, optionally с `group_id` и merge provenance.

Узел пропускает deferred records и все records, у которых
`routing_decision != ACCEPT`.

## Side effect

Создаёт или обновляет group в `JobGroupStore`.

## Логика matching

1. Exact canonical URL match: merge confidence `1.0`.
2. Identity fingerprint через `JobIdentityMatcher`: merge confidence `0.95`.
3. Optional fuzzy title/company match по blocking key: confidence =
   fuzzy score / 100 * 0.85.
4. Если match нет, создаётся новая group.

Многошаговый check-then-create защищён async lock внутри node.

## Параметры

`fuzzy_title_threshold = 85.0`, `enable_fuzzy = True`,
`attach_group_id = False`.

Если `attach_group_id = False`, side effect выполняется, но record не меняется.

## Границы

Grouping — это commit только после ACCEPT. REVIEW/REJECT не являются durable job
identity evidence.
