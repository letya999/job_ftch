---
title: "Ingest stack: источники, assessment, monitors, scrapers, parsers, bypass"
description: "Полное, но компактное описание ingest-цепочки: от SourceSpec и pre-ingest assessment до RawItem, snapshots и pipeline."
updated: 2026-07-28
---
# Ingest stack: источники, assessment, monitors, scrapers, parsers, bypass

Ingest в `job_ftch` — это не один scraper и не одна функция загрузки. Это
цепочка контрактов, где каждый слой отвечает за отдельный вопрос: что
запускать, как понять возможности источника, как найти вакансии, как извлечь
детали, когда эскалировать обход защит и в какой момент данные становятся
`RawItem` для pipeline.

## Главная линия

```text
TenantConfig / runtime overlay
  -> SourceSpec
  -> SourceAssessmentAdapter
  -> Source factory / registry
  -> Source.fetch()
  -> monitor / scraper / site_parser / bypass
  -> RawItem
  -> Pipeline / Graph
```

`SourceSpec` описывает намерение: какой источник читать и с какими параметрами.
`SourceAssessmentAdapter` оценивает источник до ingest. `Source.fetch()` уже
производит элементы. Всё, что ниже `Source.fetch()`, должно быть подготовкой
сырого входа, а не фильтрацией вакансий по профилю.

## SourceSpec и runtime overlay

Основной declarative контракт источника — `SourceSpec`. Он живёт в tenant YAML,
fixtures или runtime overlay и создаётся через registry. Для production-like
запусков источники должны приходить из tenant/runtime config, а не из env-only
ручной сборки.

Важные границы:

- `SourceSpec` не хранит секреты.
- source-specific auth разрешается через `AuthProvider`.
- runtime overlay может добавить, выключить или сузить источники без изменения
  core pipeline.
- hardcoded dispatch по source kind в core недопустим; новые backend/source
  families регистрируются через registry.

## Pre-ingest assessment

Assessment запускается до обычного fetch и отвечает на вопрос: есть ли у
источника признаки, по которым можно судить о freshness/capabilities без
полного snapshot. Он не возвращает `RawItem` и не участвует в profile relevance.

Типичные результаты assessment:

- источник умеет incremental freshness;
- freshness не доказан, нужен snapshot;
- probe failed: среда или сеть не позволили сделать вывод;
- probe blocked: источник ответил, но доступ к нужным сигналам заблокирован;
- registry hints уже знают board family или site parser capability.

Подробно: [Source assessment](source_assessment.md).

## Career-site stack

Для career sites ingest разделён на несколько ролей.

`monitor` ищет вакансии на listing/source page. Он должен быть дешёвым и
ограниченным по работе: найти URLs, embedded state, RSS/API payload или
достаточно богатую карточку вакансии. Monitor не должен превращаться в тяжёлый
detail scraper.

`scraper` работает с конкретным vacancy/detail URL. Он извлекает текст,
структурированные поля и metadata, из которых потом формируется `RawItem`.

`site_parser` — site-specific fast path. Он нужен для известных семейств и
сложных сайтов, где generic HTML path даёт низкую точность или нестабильность.
Parser должен быть подключаемым через registry/catalog, а не через разрастающийся
`if/elif` в core.

`bypass_strategy` — инфраструктурная capability вокруг transport/browser path.
Она не принимает продуктовых решений и не решает, релевантна ли вакансия.

Подробнее о ролях monitor/scraper: [Career Site Engines](../entities/career_site_engines.md).

## Telegram, RSS, APIs и fixtures

Не все источники проходят через career-site stack.

- Telegram sources читают channel/group/comment history через Telethon-backed
  source и используют Telegram-specific auth/session.
- RSS sources обычно имеют явный freshness signal на уровне feed item.
- API sources могут отдавать rich payload сразу и не нуждаться в browser path.
- `local_fixture` используется для dev/eval/test и должен оставаться дешёвым
  способом воспроизведения pipeline behavior.

Общее правило одно: какой бы backend ни использовался, наружу он отдаёт
`RawItem` или quarantine/rejected-compatible результат через общий source
contract.

## Snapshot, dedup и source health

Freshness в ingest и dedup в pipeline — разные вещи.

`SnapshotFilterNode` работает как cost optimisation между запусками: unchanged
content можно пропустить, changed content надо переиграть даже при том же URL.

`DedupNode` работает уже по item identity/fingerprint и защищает pipeline от
повторов на уровне содержимого и canonical vacancy flow.

`source health` — runtime состояние источника: failures, pause/probe logic,
drift и degraded behavior. Оно не должно подменять terminal relevance decision.

## Ошибки, которые ломают слой

- source assessment начинает вызывать LLM или pipeline nodes;
- monitor скачивает все detail pages и превращается в scraper;
- parser knowledge дублируется в hardcoded списке вместо registry hints;
- bypass начинает решать product policy;
- source silently swallows auth/blocking failures как пустой результат;
- env используется как основная модель настройки источников вместо tenant YAML.

## Где смотреть код

- `job_ftch/domain/source_spec.py`
- `job_ftch/application/source_assessment.py`
- `job_ftch/application/source_inputs.py`
- `job_ftch/application/tenant_runner.py`
- `job_ftch/infrastructure/sources/`
- `job_ftch/infrastructure/sources/monitors/`
- `job_ftch/infrastructure/sources/site_parsers/`
- `job_ftch/infrastructure/bypass/`
- [source_stack_reference.md](source_stack_reference.md)

## Связанные документы

- [Source setup](setup.md)
- [Source coverage matrix](coverage_matrix.md)
- [Справочник source stack](source_stack_reference.md)
- [Career-site runtime flow](career_site_runtime.md)
- [Browser lifecycle и teardown](browser_lifecycle.md)
- [Deadlines и concurrency](deadlines_and_concurrency.md)
- [Source assessment](source_assessment.md)
- [Bypass и escalation path](bypass_and_escalation.md)
- [SourceSpec](../entities/source_spec.md)
- [Source](../entities/source.md)
- [Source Assessment Adapter](../entities/source_assessment_adapter.md)
