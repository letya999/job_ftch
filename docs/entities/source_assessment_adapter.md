---
title: "SourceAssessmentAdapter"
description: "Pre-ingest contract для оценки capabilities, freshness и bypass needs источника."
updated: 2026-07-28
---
# SourceAssessmentAdapter

`SourceAssessmentAdapter` — application-level контракт, который оценивает
`SourceSpec` до обычного `Source.fetch()`.

## Что решает

Assessment отвечает на вопросы:

- есть ли у источника incremental freshness signal;
- можно ли доверять API/feed/monitor metadata;
- нужен ли snapshot fallback;
- нужен ли browser/bypass route;
- насколько уверенно это известно.

Assessment не отвечает на вопрос “подходит ли вакансия пользователю”.

## Вход и выход

**Вход:** `SourceSpec` и `SourceAssessmentContext`.

**Выход:** `SourceAssessmentResult`, где есть:

- `SourceCapabilities`;
- `SourceEvidence`;
- `FreshnessAssessment`;
- confidence/probe state.

`probe_failed` и `probe_blocked` различают “probe не смог завершиться” и
“probe завершился, но источник заблокировал/потребовал другой route”.

## Built-in adapters

- `TelegramSourceAssessmentAdapter` — high-confidence Telegram incremental
  assessment.
- `RSSSourceAssessmentAdapter` — feed freshness assessment.
- `KnownSourceAssessmentAdapter` — registry hints от известных APIs, monitors
  и site parsers.
- `GenericSourceAssessmentAdapter` — conservative fallback.
- `CareerSiteAssessmentEngine` — bounded probe для career-site URL shape,
  monitor hints, structured metadata и bypass needs.

## Границы

Assessment adapters не должны:

- создавать `RawItem`;
- вызывать LLM;
- исполнять pipeline nodes;
- принимать relevance/routing/posting decision;
- хранить секреты;
- держать второй hardcoded catalog parser/monitor knowledge.

Они могут читать безопасные поля `SourceSpec`, registry hints и bounded
runtime probes. Auth допускается только через разрешённые runtime boundaries.

## Где смотреть код

- `job_ftch/application/source_assessment.py`
- `job_ftch/infrastructure/source_assessment/`
- `job_ftch/domain/source_assessment.py`
- `job_ftch/application/registry.py`

## Связанные документы

- [Source](source.md)
- [SourceSpec](source_spec.md)
- [Career Site Engines](career_site_engines.md)
- [Справочник source stack](../sources/source_stack_reference.md)
- [Source assessment](../sources/source_assessment.md)
