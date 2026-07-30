---
title: "Browser lifecycle и teardown"
description: "Жизненный цикл browser-сессий в ingest: open_page, слот конкурентности, patchright cancellation-фиксы, session-bypass path, реапинг драйверов и полный teardown на выходе."
updated: 2026-07-30
---
# Browser lifecycle и teardown

Browser-path используется, когда HTTP/parser-путь не может прочитать источник
(JS-рендер, challenge, fingerprint). Точка входа —
`open_page(...)` в `job_ftch/infrastructure/sources/browser_utils.py`. Этот
документ описывает, как сессия открывается, ограничивается по конкурентности и —
главное — как гарантированно завершаются дочерние browser/driver-процессы.

## open_page

```python
async with open_page(config, use_proxy=..., bypass_strategy=...) as page:
    ...
```

`open_page` — единственный санкционированный способ получить страницу. Он:

1. даёт bypass-стратегии переписать config (`prepare_browser_config`);
2. входит в глобальный слот `browser_slot(career_site_browser_concurrency)`
   (дефолт 3) — старт браузера дорог и ограничен на весь процесс;
3. выбирает путь:
   - **session-bypass path** — если стратегия предоставляет
     `BrowserSessionBypass.open_page(...)` (ADR-050): стратегия сама владеет
     browser runtime (nodriver/camoufox/cloak). `open_page` только прокидывает
     конфиг/прокси и корректно разворачивает `__aexit__` даже при отмене по
     дедлайну;
   - **patchright path** — стандартный Playwright-совместимый запуск
     (`_open_playwright_page` или `_open_persistent_page` для persistent-context).

Только `config`-ключи из `BROWSER_KEYS` пробрасываются в браузер (wait, timeout,
user_agent, headless, stealth, viewport, locale, cookies, skip_ssl, channel и т.д.).

## Конкурентность

`browser_slot` и `detail_slot` — loop-local семафоры из `shared_limiters.py`.
Захват слота проходит через `await_with_source_deadline`, поэтому источник,
исчерпавший бюджет в очереди за браузером, не создаёт «поздней» browser-работы
уже будучи terminal. Подробнее про уровни параллелизма и дедлайны —
[deadlines_and_concurrency.md](deadlines_and_concurrency.md).

## Patchright cancellation-фиксы

Patchright 1.61 при отмене task оставляет protocol-callback Future висящим; поздний
ответ браузера затем выставляет исключение на «осиротевший» Future, и Python
печатает `Future exception was never retrieved` (особенно заметно на Windows при
teardown после дедлайна). `_install_patchright_cancellation_fix()` один раз на
процесс патчит `Channel._inner_send` и `RouteHandler.handle`, чтобы отменять
callback до re-raise и не завершать уже отменённый Future. Это узкий workaround,
не общий механизм.

Закрытие ресурсов идёт от внутреннего к внешнему и best-effort с таймаутом
`_BROWSER_CLEANUP_TIMEOUT_SECONDS` (2s) на шаг: `unroute_all` -> `page.close` ->
`context.close` -> `browser.close`. Любой шаг может уже быть мёртв (target killed
патчрайтом), поэтому ошибки глотаются, но не молча — они логируются.

## Бюджеты запуска браузера

Число запусков браузера ограничено на уровне bypass-operation: listing-операция
получает `bypass_max_listing_browser_launches` (дефолт 3), detail-операция —
`bypass_max_detail_browser_launches` (дефолт 1) через `start_operation`/
`end_operation`. Это не даёт одному источнику бесконечно перезапускать браузер.

## Реапинг драйверов и teardown процессов

Проблема: bypass-стек (patchright, nodriver, camoufox, cloakbrowser) может
оставить осиротевшие browser/driver-процессы. На Windows «живой» дочерний процесс
удерживает интерпретатор от выхода, и завершённый прогон висит бесконечно.

Ключевое инвариант безопасности: реапинг работает **только** над
`psutil.Process().children(recursive=True)` текущего процесса. Собственный Chrome
пользователя находится в отдельном дереве процессов и физически не может попасть
под удар. Второй слой защиты — фильтр по именам/cmdline
(`_BROWSER_PROC_MARKERS`: chrome/chromium/patchright/playwright/camoufox/firefox/
cloak/nodriver; bare `node` — только с driver-маркером в cmdline).

Есть две функции, и различие между ними обязательно из-за конкурентности:

| Функция | Что делает | Где вызывается |
|---|---|---|
| `reap_stale_browser_drivers()` | убивает только **устаревшие** (старше `min_age_seconds`, дефолт 180) **бездетные** сироты — драйверы, чей браузер уже вышел | во всех `finally` путях `open_page`/`_open_playwright_page`/`_open_persistent_page` и в recovery `_launch_browser_with_recovery` |
| `terminate_browser_descendants()` | terminate -> wait(grace 5s) -> kill для **всех** browser-потомков | полный teardown на выходе прогона/процесса |

Почему две: с Phase-7B (ADR-047) `Pipeline.run` держит несколько `open_page`
одновременно (дефолт item-concurrency 4). Полное убийство дерева в per-`open_page`
`finally` уничтожило бы браузеры соседних параллельных скрейпов. Поэтому
per-page путь трогает только устаревшие бездетные сироты (безопасно при
конкуренции), а полный teardown зарезервирован для момента, когда никакая
browser-сессия уже не нужна.

Точки полного teardown:

- `scripts/run_ingest_batch.py` — `finally` в `__main__` после `asyncio.run(main())`;
- `job_ftch/application/tenant_runner.py` — `TenantRunner.close()` (`finally`
  после закрытия стораджей), покрывает production-прогоны.

Чистое ядро отбора устаревших сирот вынесено в `_select_stale_driver_pids`
(работает над снимком `(pid, ppid, age, is_browser)`), что делает логику
unit-тестируемой без запуска реальных браузеров. Все функции — no-op, если
`psutil` не установлен, и никогда не бросают. Регресс-тесты:
`tests/test_browser_reaper.py`.

## Границы

- browser-path не принимает relevance-решений и не парсит вакансии — это делают
  monitor/scraper/parser поверх страницы;
- teardown никогда не трогает процессы, которые не породил сам (только потомки
  текущего PID);
- session/challenge-операции ограничены по бюджету и не превращаются в постоянный
  daemon (см. [bypass_and_escalation.md](bypass_and_escalation.md)).

## Где смотреть код

- `job_ftch/infrastructure/sources/browser_utils.py`
- `job_ftch/infrastructure/sources/shared_limiters.py`
- `job_ftch/infrastructure/bypass/nodriver_bypass.py`,
  `camoufox_bypass.py`, `cloak_bypass.py`, `stealth_browser.py`
- `scripts/run_ingest_batch.py`, `job_ftch/application/tenant_runner.py`
- ADR-022, ADR-047, ADR-050, ADR-073

## Связанные документы

- [Career-site runtime flow](career_site_runtime.md)
- [Deadlines и concurrency](deadlines_and_concurrency.md)
- [Bypass и escalation path](bypass_and_escalation.md)
- [BypassStrategy](../entities/bypass_strategy.md)
