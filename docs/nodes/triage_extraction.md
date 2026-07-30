---
title: "TriageExtractionNode"
description: "Core-only extraction + RelevanceCard для relevance-card experiment."
updated: 2026-07-27
---
# TriageExtractionNode

`TriageExtractionNode` создаёт только поля, нужные для ранней оценки
релевантности, и сохраняет compact `RelevanceCard` в draft metadata.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `JobDraft` с `metadata.relevance_card`.

## Логика

Внутри используется `ExtractionNode(scope="core")`. После получения draft узел
строит `RelevanceCard`: title, employer, seniority, role anchors, location,
salary presence, первые 2000 символов text и до трёх evidence spans из
responsibilities.

## Границы

Full field enrichment намеренно отложен до `FullExtractionNode` после
terminal routing. Узел не заменяет обычный full extraction path.
