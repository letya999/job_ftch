---
title: "RiskScoringNode"
description: "Отдельный risk scoring без смешивания с relevance score."
updated: 2026-07-27
---
# RiskScoringNode

`RiskScoringNode` ищет простые risk signals в `JobRecord` и считает
`risk_score`/`risk_level`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с обновлёнными `risk_signals`, `risk_score`,
`risk_level`, review reasons и metadata `risk_score`.

## Логика

Узел добавляет `suspicious_domain`, если title/company/description содержат
crypto, nft, casino, betting или mlm.

`contact_only_apply_flow` добавляется для Telegram+DM apply flow без
canonical URL.

`low_information_density` добавляется для очень короткого description.

Risk score = `0.2 * unique_signals`, capped at 1.0. Level: LOW, MEDIUM от
0.4, HIGH от 0.75.

## Параметры

`review_threshold = 0.45`. Если score выше порога, добавляется
`high_risk_signals` в review reasons.

## Границы

Risk отделён от relevance: риск не означает “не подходит профилю”, но может
дать veto/evidence для decision policy.
