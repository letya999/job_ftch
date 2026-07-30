---
title: "SourceSpec"
description: "**Слой**: `domain`"
updated: 2026-07-24
---
# SourceSpec

**Слой**: `domain`
**Файл**: `job_ftch/domain/source_spec.py`

## Что это

`SourceSpec` — discriminated union конфигураций источников.

Это безопасное описание того, что и как читать. Секреты сюда не кладутся;
секреты разрешаются отдельно через `AuthProvider`.

`SourceSpec` не является результатом source intelligence. Он описывает locator
и базовые параметры, а `SourceAssessmentAdapter` отдельно сохраняет runtime
знание о capabilities, incremental strategy и bootstrap plan.

## Общая база

Все source specs наследуют `BaseSourceSpec`, где уже есть:

- `interval_seconds`
- `rate_limit_min_interval_seconds`
- `rate_limit_backoff_multiplier`
- `ingest_mode`
- `bypass`
- `bypass_config`

## Текущие builtin spec types

- `telegram_channel`
- `telegram_group`
- `telegram_comments`
- `declarative_html`
- `career_site`
- `local_fixture`
- `rest_api`
- `browser`
- `rss_feed`
- `telegram_realtime`
- `lever`
- `webhook`
- `websocket`

## Особо важный пример: CareerSiteSpec

`CareerSiteSpec` содержит поля для многошагового scraping path:

- `url`
- `limit`
- `source_name`
- `monitor`
- `monitor_config`
- `scraper`
- `scraper_config`
- `scraper_fallback`
- `detail_limit`
- `url_filter`
- `url_transform`

## Практические правила

- `SourceSpec` должен быть безопасен для хранения в git
- `type` обязан однозначно выбирать factory
- новые source types добавляются через registry, не через central switch
- новый `SourceSpec` не добавляется только ради assessment; assessment живёт
  рядом с ingest, а не внутри source config

## Связанные документы

- [Source](source.md)
- [AuthProvider](auth_provider.md)
- [TenantConfig](tenant_config.md)
