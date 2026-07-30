---
title: "AIRoleRelevanceNode"
description: "Legacy/custom AI-role relevance gate для Job."
updated: 2026-07-27
---
# AIRoleRelevanceNode

`AIRoleRelevanceNode` — legacy/custom relevance gate для старого `Job` model.
В default pipeline не подключён.

## Вход и выход

**Вход:** `Job`.

**Выход:** `Job`, если role relevance проходит.

**Drop:** `RawItemDropped(reason=JOB_OUT_OF_SCOPE)`, если job совпал с
negative keywords, LLM `ai_relevance` ниже threshold, post type candidate/spam,
или fallback keyword score ниже threshold.

## Параметры

`profile: FilterProfile`, default `FilterProfile.default()`.

## Логика

Negative keywords проверяются всегда. Если extraction уже дал `ai_relevance`,
используется он. Если `ai_relevance` отсутствует, узел применяет простое
keyword scoring по `positive_relevance_keywords`.

## Границы

Это старый hard gate. Current production relevance строится через profile
scores, LLM/evidence atoms и `EvidenceDecisionNode`.
