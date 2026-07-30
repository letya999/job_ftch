---
title: "EvidenceFanOutNode"
description: "Единственная parallel fan-out стадия typed evidence producers."
updated: 2026-07-27
---
# EvidenceFanOutNode

`EvidenceFanOutNode` запускает bounded набор `EvidenceProducer` и агрегирует
их `EvidenceAtom` в `AssessedJob`.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `AssessedJob` с `evidence`, `assessments`, `policy_version` и
`degradation_reasons`.

## Producers по умолчанию

`MetadataEvidenceProducer` читает transitional serialized atoms из metadata.

`ProfileScoreEvidenceProducer` превращает per-profile features в auditable
PROFILE_RELEVANCE и HARD_CONSTRAINT atoms.

`ProfileLexicalEvidenceProducer` превращает negative lexical matches в
typed contradiction evidence.

`RiskEvidenceProducer` превращает `risk_signals` в RISK evidence.

`LifecycleQualityEvidenceProducer` создаёт FRESHNESS и EVIDENCE_QUALITY atoms.

## Параметры

`parameters` — claim/source-family policy parameters.

`policy_version` — версия evidence policy.

`timeout_seconds = 2.0` — общий timeout fan-out.

## Логика

Producer tasks запускаются параллельно. Timeout или exception отдельного
producer не падает весь pipeline: причина записывается в `degradation_reasons`.
Atoms сортируются по `evidence_id`, затем `aggregate_bundle()` строит
claim assessments.

## Границы

Fan-out не выбирает routing lane. Он только собирает typed evidence и
assessments для `DecisionNode`.
