---
title: "Source assessment"
description: "Pre-ingest оценка источников: capability hints, freshness evidence, probe outcomes и границы ответственности."
updated: 2026-07-28
---
# Source assessment

`SourceAssessmentAdapter` — это pre-ingest слой. Он оценивает `SourceSpec` до
того, как normal ingest начнёт читать элементы. Его задача — понять capability
источника и freshness evidence, а не добыть вакансии.

## Зачем он нужен

Без assessment runtime вынужден либо каждый раз делать полный snapshot, либо
верить source-specific предположениям в коде. Assessment делает это явным:
источник либо доказал признаки freshness, либо не доказал, либо probe не смог
дать надёжный ответ.

Это важно для:

- career sites без явного updated timestamp;
- Telegram/RSS/API источников с разными incremental guarantees;
- runtime source overlays, где новые источники добавляются без релиза кода;
- source health, pause/probe и degraded runtime behavior;
- reproducible eval/live comparisons, где нужно понимать, что было пропущено
  как unchanged, а что вообще не удалось проверить.

## Вход и выход

Вход: `SourceSpec`, registry hints, дешёвые runtime hints и bounded probe.

Выход обычно раскладывается на:

- `SourceCapabilities`;
- `SourceEvidence`;
- `FreshnessAssessment`;
- persisted source-scoped run state.

Assessment должен различать:

- freshness доказан;
- freshness не доказан;
- probe failed;
- probe blocked;
- capability known from registry hints;
- conservative fallback.

## Builtin families

Telegram assessment фиксирует Telegram-specific incremental profile. Здесь
главное не парсить сообщения, а корректно объявить known behavior source family.

RSS assessment использует feed-level freshness expectations: item timestamps,
ids и feed update semantics.

Known source assessment агрегирует registry hints от monitor/site-parser/API
catalog. Это основной путь для известных ATS/board families.

Generic source assessment отвечает консервативно. Если источник не доказал
freshness, runtime должен считать snapshot path необходимым.

## Career-site probe

Generic career-site assessment может использовать bounded часть scraping stack:

- site parser defaults;
- site fingerprinter;
- monitor `can_handle` и `assessment_probe`;
- scraper `can_handle` и `assessment_probe`;
- response headers;
- embedded state;
- малую выборку detail URLs.

Важно: это не обычный `Source.fetch()`. Probe ограничен по бюджету, не вызывает
LLM и не запускает pipeline graph.

## Границы

Assessment не должен:

- создавать `RawItem`;
- вызывать LLM;
- исполнять pipeline nodes;
- принимать relevance/routing/posting decision;
- хранить секреты;
- переписывать registry knowledge в отдельный hardcoded catalog.

Assessment может:

- читать безопасные поля `SourceSpec`;
- использовать auth только через разрешённые runtime boundaries;
- читать registry hints;
- делать bounded HTTP/browser probe, если это часть текущей source family;
- писать результат в source-scoped runtime state.

## Как результат используется

Tenant/runtime layer использует assessment для source health и freshness
strategy. Если freshness не доказан, это не значит “источник плохой”; это
значит “нельзя безопасно полагаться на incremental freshness без snapshot”.

Pipeline relevance path assessment не читает. Terminal decision принимает graph,
а не source assessment.

## Где смотреть код

- `job_ftch/application/source_assessment.py`
- `job_ftch/infrastructure/source_assessment/`
- `job_ftch/application/tenant_runner.py`
- `job_ftch/infrastructure/sources/career_site_source.py`
- `job_ftch/infrastructure/sources/monitors/`
- `job_ftch/infrastructure/sources/site_parsers/`

## Связанные документы

- [Ingest stack](ingest_stack.md)
- [Справочник source stack](source_stack_reference.md)
- [Source Assessment Adapter](../entities/source_assessment_adapter.md)
- [Career Site Engines](../entities/career_site_engines.md)
- [SourceSpec](../entities/source_spec.md)
