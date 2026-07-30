---
title: "Career Site Engines"
description: "Monitor, scraper, site parser и bypass роли внутри career-site ingestion."
updated: 2026-07-28
---
# Career Site Engines

Career-site ingestion разбит на несколько ролей. Это защищает систему от
монолитных “парсеров всего сайта”, которые сложно поддерживать и невозможно
безопасно эскалировать.

## Главные роли

| Роль | Где живёт | Что делает |
|---|---|---|
| Monitor | `job_ftch/infrastructure/sources/monitors/` | находит vacancy URLs или structured posting payloads |
| Scraper | `job_ftch/infrastructure/sources/scrapers/` | извлекает text/metadata из detail page или payload |
| SiteParser | `job_ftch/infrastructure/sources/site_parsers/` | domain-specific parser для сайтов, где generic extraction недостаточен |
| BypassStrategy | `job_ftch/infrastructure/bypass/` | меняет access route, browser/session/proxy/captcha поведение |
| SourceAssessmentAdapter | `job_ftch/infrastructure/source_assessment/` | до ingest оценивает capabilities/freshness/bypass needs |

## Monitor

Monitor должен быть лёгким discovery layer. Он проверяет board/listing/API/feed
и возвращает `MonitorResult`, URL set/list или `DiscoveredPostingPayload`.

Monitor может:

- найти API endpoint или RSS/sitemap;
- понять known ATS family;
- вернуть structured payload без detail-page scraping;
- дать registry assessment hint.

Monitor не должен:

- скачивать все detail pages без бюджета;
- решать relevance;
- скрывать access/blocking failures как “нет вакансий”.

## Scraper

Scraper работает уже с конкретной detail page или structured payload. Он
извлекает user-visible vacancy text и metadata, которые затем превращаются в
`RawItem`.

Scraper может использовать JSON-LD, embedded state, Next.js data, DOM/XPath,
main text extraction или board-specific logic.

Scraper не должен принимать terminal policy decision. Максимум — явно вернуть
отсутствие usable vacancy payload или degraded extraction signal.

## SiteParser

`SiteParser` — domain-specific parser protocol. Модули self-register’ятся через
`register_site_parser(name, domain_pattern=...)`.

Site parser нужен, когда generic monitor/scraper не покрывает конкретный сайт:
нестандартная DOM-структура, специфичные fields, hidden embedded state,
нестабильный listing shape.

Новый parser должен жить в `site_parsers/`, а не в `config.py`, builder или
core pipeline.

## Bypass

Bypass меняет способ доступа: HTTP impersonation, browser context, stealth
hardening, proxy, session handoff, CAPTCHA handling. Он не парсит вакансии и
не решает product policy.

Эскалация должна идти через source assessment, failure signals, route state и
budgets. Если сайт изменил HTML, это parser/scraper issue, а не повод
бесконечно вращать proxy.

## Как flow выглядит целиком

```text
CareerSiteSpec
  -> SourceAssessmentAdapter / CareerSiteAssessmentEngine
  -> monitor discovery
  -> detail scraper or site parser
  -> RawItem
  -> pipeline nodes
```

## Где смотреть код

- `job_ftch/infrastructure/sources/career_site_source.py`
- `job_ftch/infrastructure/sources/career_monitor_runner.py`
- `job_ftch/infrastructure/sources/career_detail_runner.py`
- `job_ftch/infrastructure/sources/monitors/`
- `job_ftch/infrastructure/sources/scrapers/`
- `job_ftch/infrastructure/sources/site_parsers/`
- `job_ftch/infrastructure/bypass/`

## Связанные документы

- [SourceSpec](source_spec.md)
- [Source](source.md)
- [SourceAssessmentAdapter](source_assessment_adapter.md)
- [BypassStrategy](bypass_strategy.md)
- [Справочник source stack](../sources/source_stack_reference.md)
