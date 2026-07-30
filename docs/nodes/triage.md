---
title: "HeuristicTriageNode"
description: "Legacy/custom early RawItem triage по FilterProfile."
updated: 2026-07-27
---
# HeuristicTriageNode

`HeuristicTriageNode` — ранний heuristic gate для `RawItem`. В default pipeline
он не подключён; используется для custom configurations и tests.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem`, если triage пройден.

**Drop:** `RawItemDropped` с `TriageRejectionReason`, если source kind
запрещён, текст слишком короткий, нет required keywords или source-specific
signal недостаточен.

## Параметры

`profile: FilterProfile`, default `FilterProfile.default()`.

## Логика

Общие проверки: allowed source kinds, minimum token/char length, required
keywords.

Career site: нужен positive relevance keyword или stable job-like metadata;
navigation/company pages дропаются как `CAREER_SITE_NON_JOB_PAGE`.

Telegram comments: нужны follow-up/vacancy signals вроде candidate, resume,
salary, relocation, remote, stack.

Telegram channel/group: нужен positive relevance keyword; exclude keywords
дают `IRRELEVANT_CONTENT`, иначе `TELEGRAM_LOW_SIGNAL`.

## Границы

Это legacy/custom heuristic. Production path предпочитает evidence-producing
узлы, чтобы спорные cases доходили до decision layer.
