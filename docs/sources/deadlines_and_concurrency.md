---
title: "Deadlines и concurrency"
description: "Бюджеты времени и параллелизм ingest: source deadline scope, hard/soft/watchdog, per-source и global concurrency, loop-local limiters и их взаимодействие с teardown."
updated: 2026-07-30
---
# Deadlines и concurrency

Ingest должен быть ограничен по времени и по параллелизму: один медленный или
защищённый источник не имеет права съесть весь прогон, а дорогие browser/detail
операции не должны перегружать хост. Этот документ описывает фактический механизм.

## Source deadline (per-source wall-clock)

`job_ftch/infrastructure/sources/source_deadline.py` хранит абсолютный monotonic
дедлайн в `ContextVar`. Он ставится на задачу источника через
`source_deadline_scope(deadline_at)` и читается всеми I/O-хелперами:

- `await_with_source_deadline(awaitable)` — если бюджет исчерпан, немедленно
  бросает `TimeoutError("source deadline exhausted")` (и закрывает ещё не
  запущенную корутину, чтобы не было unawaited-warning). Иначе оборачивает работу
  в дочернюю task и при таймауте **отменяет и дренирует** её. Важно: обёртка не
  использует `asyncio.timeout` напрямую вокруг Patchright — отмена во время, пока
  драйвер владеет response-future, приводит к «Future exception was never
  retrieved» на Windows; собственная task позволяет отменить и дождаться drain;
- `sleep_with_source_deadline(seconds)` — сон, обрезанный остатком бюджета;
- `remaining_source_seconds()` — сколько бюджета осталось.

Так дедлайн прозрачно доходит до навигации, ожидания слотов, detail-fetch и
teardown, без ручного проброса таймаутов через каждый вызов.

## Hard / soft / watchdog

| Настройка | Дефолт | Смысл |
|---|---|---|
| `source_soft_deadline_seconds` | 45.0 | мягкая граница «пора закругляться» |
| `source_hard_deadline_seconds` | 120.0 | жёсткий бюджет источника (ставится в scope) |
| `source_hard_cancel_grace_seconds` | 0.1 | добавка к hard для bounded cleanup |

В `scripts/run_ingest_batch.py` поверх этого есть внешний watchdog: если task
живёт дольше `timeout + hard_cancel_grace`, он принудительно отменяется и
источнику ставится `deadline_exceeded`. Дедлайн — это операционный исход
(`terminal_outcome="deadline_exceeded"`), а не доказательство пустой борды (см.
`_failure_bucket`). Политика бюджетов зафиксирована в ADR-072.

## Уровни concurrency

Параллелизм многоуровневый; каждый уровень независим и настраивается.

| Уровень | Настройка | Дефолт | Ограничивает |
|---|---|---|---|
| pipeline items | `pipeline_item_concurrency` (+`_adaptive`) | 4 | одновременно обрабатываемые item-воркеры в `Pipeline.run` (ADR-047) |
| browser starts | `career_site_browser_concurrency` | 3 | глобальный `browser_slot` — одновременные запуски браузера на процесс |
| detail (per-source) | `career_site_detail_concurrency` | 8 | воркеры detail-страниц внутри одного источника |
| detail (global) | `career_site_global_detail_concurrency` | 16 | глобальный `detail_slot` на все источники |

`pipeline_item_concurrency_adaptive=true` позволяет runtime поднимать фактический
уровень выше запрошенного при тяжёлых I/O-миксах и не оверсабскрайбить маленькие
прогоны (стратегия stdlib-only, без psutil — ADR-047).

## Loop-local limiters

`shared_limiters.py` держит семафоры в `WeakKeyDictionary` по event loop и по паре
`(name, capacity)`. Это даёт единый глобальный лимит на процесс/loop (а не
локальный семафор на каждый вызов, который игнорировал бы настройку). Захват слота
идёт через `await_with_source_deadline`, поэтому:

```text
источник исчерпал дедлайн в очереди за browser_slot
  -> acquire бросает TimeoutError
  -> источник завершается terminal, поздняя browser-работа не создаётся
```

## Bypass operation budgets

Поверх времени и слотов bypass ограничивает число запусков браузера на операцию:
`start_operation(kind="listing", max_browser_launches=bypass_max_listing_browser_launches=3)`
для листинга и `kind="detail"` с `bypass_max_detail_browser_launches=1` для каждой
detail-страницы. Также действуют per-operation route-attempts, proxy-rotation и
weighted work budgets (см. [bypass_and_escalation.md](bypass_and_escalation.md)).

## Взаимодействие с teardown

Дедлайн и конкурентность прямо связаны с завершением browser-процессов:

- отмена по дедлайну разворачивает `finally` в `open_page`, где вызывается
  `reap_stale_browser_drivers()` (безопасный при конкуренции реап устаревших сирот);
- полный `terminate_browser_descendants()` вызывается только когда все item-воркеры
  завершены (выход прогона/процесса), иначе он убил бы браузеры живых соседних
  источников.

Детали — [browser_lifecycle.md](browser_lifecycle.md).

## Где смотреть код

- `job_ftch/infrastructure/sources/source_deadline.py`
- `job_ftch/infrastructure/sources/shared_limiters.py`
- `job_ftch/application/concurrency.py`, `job_ftch/application/builder.py`
- `job_ftch/config.py` (секции career-site и deadline settings)
- `scripts/run_ingest_batch.py`
- ADR-047, ADR-072

## Связанные документы

- [Career-site runtime flow](career_site_runtime.md)
- [Browser lifecycle и teardown](browser_lifecycle.md)
- [Bypass и escalation path](bypass_and_escalation.md)
- [Ingest stack](ingest_stack.md)
