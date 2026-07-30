---
title: "Source"
description: "`Source` — это поставщик входящих элементов для пайплайна."
updated: 2026-07-24
---
# Source

## Что это

`Source` — это поставщик входящих элементов для пайплайна.

Формально это protocol:

```python
def fetch(self) -> AsyncIterator[SourceItem | QuarantinedRawItem]
```

Pipeline не должен знать, откуда пришли данные: из Telegram, RSS, API,
career site, fixture или runtime overlay.

Pre-ingest classification выполняется отдельно через
`SourceAssessmentAdapter`. Source остаётся контрактом получения `RawItem`, а не
местом выбора bootstrap, coverage или freshness strategy.

## Что source обязан делать

- выдавать валидные `RawItem`
- либо явно отдавать `QuarantinedRawItem`, если raw payload сломан ещё до `RawItem`
- скрывать детали внешнего API, pagination, retries и auth

## Что source не должен делать

- втаскивать business logic матчинга или routing
- назначать incremental ingest strategy для самого себя
- сохранять секреты внутри `SourceSpec`
- возвращать "пустые" raw items без текста и идентичности

## Текущие встроенные группы sources

- Telegram polling sources
- `CareerSiteSource`
- declarative HTML source path
- fixture source
- RSS source
- selected API sources
- realtime/push-oriented specs и экспериментальные реализации

## Особенность career-site path

Career-site ingestion состоит не из одного класса, а из стека:

- `SourceSpec`
- optional `site_parser`
- `monitor`
- `scraper`
- `bypass_strategy`

Поэтому для новых сайтов часто правильнее добавлять parser/monitor/scraper, а
не новый top-level source type.

## Связанные документы

- [SourceSpec](source_spec.md)
- [SourceAssessmentAdapter](source_assessment_adapter.md)
- [RawItem](raw_item.md)
- [Store](store.md)
- [AuthProvider](auth_provider.md)
