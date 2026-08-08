---
title: "Career-site runtime flow"
description: "Фактический control-flow CareerSiteSource.fetch(): strategy init, site parser, generic search, monitor chain, discover/enrich, freshness, zero-yield taxonomy и teardown."
updated: 2026-07-30
---
# Career-site runtime flow

Этот документ описывает, что реально происходит внутри
`CareerSiteSource.fetch()` (`job_ftch/infrastructure/sources/career_site_source.py`).
Обзорные документы ([ingest_stack.md](ingest_stack.md),
[source_stack_reference.md](source_stack_reference.md)) фиксируют роли слоёв;
здесь фиксируется порядок шагов и правила остановки.

## Главная линия fetch()

```text
_init_strategy            -> domain, cached strategy, bypass, BypassContext
start_operation(listing)  -> per-source browser-launch budget
_try_site_parser          -> site-specific fast path (может завершить fetch)
_maybe_apply_generic_search -> keyword-search URL rewrite (если нет parser)
_resolve_monitors         -> auto-detect или explicit monitor chain
_run_monitor_chain        -> discover -> enrich (основной объём вакансий)
finally                   -> end_operation, close bypass, close http, intel.save
```

`fetch()` — async generator, отдающий `RawItem`/`QuarantinedRawItem`. На каждом
шаге он копит наблюдаемость в `FetchStats` (`stats.to_log_dict()`) и, при нулевом
результате, выставляет `ZeroYieldReason` (см. ниже).

## Strategy init и cached strategy

`_init_strategy` определяет `domain`, отмечает filtered-listing URL (наличие
`q`/`query`/`text`/`search`/`filter` в query) и, если источник в auto-режиме,
поднимает cached strategy из `store.get_source_strategy(domain)`. Cached monitor и
bypass используются как стартовая точка: `bypass_strategy.escalate_to(cached_bypass)`
сразу поднимает route на ранее сработавший tier, чтобы не переигрывать эскалацию с
нуля. Затем создаётся `BypassContext.for_url(...)` и связывается со стратегией
(`bind_context`).

При успешном ingest `_run_monitor_chain` вызывает
`store.save_source_strategy(domain, monitor, bypass_tier)` — так strategy
самообучается по доменам между запусками (только для auto-источников).

## Site parser fast path

`_try_site_parser` берёт `resolve_site_parser(url)`. Если у парсера есть
`has_custom_parse`, он идёт вперёд обычного monitor-пути:

- `supports_discover=True`: `discover()` возвращает список detail URL, которые
  считаются trusted (`_trusted_parser_urls`) и сразу уходят в enrich-фазу.
- иначе: `parse()` отдаёт готовые payloads, они фильтруются по freshness и
  дедуплицируются (`_dedupe_parser_items`).

Терминальные флаги парсера останавливают fetch и **запрещают** generic-fallback,
чтобы не превращать факт «пусто/защищено» в шумный обход всего сайта:

- `confirmed_empty_on_empty` -> `CONFIRMED_EMPTY`;
- `terminal_on_empty` -> `BLOCKED_NO_BYPASS_LEFT`;
- protected error (401/403/429) -> `BLOCKED_NO_BYPASS_LEFT`.

Если parser отдал элементы или выставил терминальный флаг — `fetch()` завершается.

## Generic keyword-search rewrite

Если dedicated site parser отсутствует и в URL ещё нет явного запроса,
`_maybe_apply_generic_search` пытается переписать `spec.url` на search-URL,
собранный из `target_roles`. Подробнее — [search_query_ingest.md](search_query_ingest.md).
На любой ошибке исходный URL сохраняется (never-fail).

## Monitor resolution и chain

`_resolve_monitors`: в auto-режиме `monitor_detector.get_ordered_monitors(url)`
фингерпринтит страницу и возвращает упорядоченный список мониторов и detected
config; иначе берётся явный monitor. В хвост всегда добавляются fallback
`api_sniffer` и `dom`. Для filtered-listing URL без `url_filter` из цепочки
убирается `sitemap` (широкий инвентарь сайта не нужен для отфильтрованного листинга).

`_run_monitor_chain` для каждого монитора крутит внутренний retry-цикл:

1. `apply_bypass_http` -> `run_monitor_attempt`;
2. специальные исключения: `AtsRedirectException` (переключиться на ATS-монитор и
   обрезать остаток цепочки), `BrowserChallengeError` (эскалация или
   `BLOCKED_NO_BYPASS_LEFT`), `BoardGoneError` (`BOARD_GONE`);
3. прочие ошибки -> `_try_escalate_bypass` (см. ниже); если tier сменился —
   повтор того же монитора, иначе переход к следующему;
4. `apply_url_filter` / `apply_url_transform`; флаги `confirmed_empty` / `board_gone`;
5. если ничего не найдено и это не последний монитор — `MONITOR_EMPTY`, следующий
   монитор. Пустой монитор эскалируется в browser-route только при явном
   `url_filter` или `render`-подсказке (`_should_escalate_empty_monitor`), иначе
   обычная пустота не тратит source-бюджет на запуск браузера.

### Эскалация bypass внутри цепочки

`_try_escalate_bypass` — единственная точка смены route. Если у стратегии есть
`handle_failure`, ей передаются `status_code`, headers, body, error, `retry_after`;
классификатор (FailureSignal) выбирает ось route (transport/network/browser/session/
challenge — см. [bypass_and_escalation.md](bypass_and_escalation.md)). Возврат
`True` означает «tier/route сменился, повтори»; `False` — «эскалации нет, идём
дальше». Legacy-адаптеры без `handle_failure` используют упрощённый
`classify_error` + `escalate()`.

Residential-proxy tier включается только как rescue-route после сигналов защиты
и дополнительно режется настройками `proxy_rescue_allow_domains` /
`proxy_rescue_deny_domains`. Для DataImpulse первая рабочая политика: RU,
gateway `gw.dataimpulse.com:823`, 1 GB общий бюджет на процесс/ран,
0.05 GB per-domain budget, allow:
`career.habr.com`, `careers.higgsfield.kz`, `www.epam.com`, `careers.epam.com`;
deny: `tbank.ru`, `rabota.sber.ru`, `*.gov`, `*.gov.ru`, `*.gosuslugi.ru`.
То есть обычные сайты продолжают ходить direct/browser-tier, а платный трафик
тратится только на источники, которые раньше стабильно упирались в CAPTCHA или
IP-block. Provider-neutral proxy primitives описаны в
[ADR-079](../adr/079-proxy-provider-pool-primitives.md).

## Discover -> enrich (двухфазная модель)

Успешный монитор даёт `MonitorResult`, который превращается в кандидатов и затем в
`RawItem`.

**Discover (`_discover_candidates`)** нормализует результат в
`DiscoveredCandidate` со scored lifecycle:

- rich ATS payloads (есть `payloads_by_url`) получают `score_completeness` и
  метку `monitor_type`; они уже полны и не требуют detail-fetch;
- URL-only ссылки идут на scrape; для `sitemap` остаются только URL со
  score `>= _SITEMAP_DETAIL_SCORE_MINIMUM` (5) — корпоративный sitemap это
  инвентарь сайта, не листинг вакансий;
- frontier ограничен candidate cap = `max(effective_limit * 20, 100)`; при
  превышении URL ранжируются (`_rank_detail_urls`) и обрезаются, ставится
  `truncated`.

**Enrich (`_enrich_candidates`)**:

- rich-кандидаты эмитятся напрямую (с учётом `spec.limit`), `rich_emitted++`;
- URL-only: pre-dedup через `store.has_processed` (fail-open), ранжирование, затем
  `_iter_scraped_detail_items` гоняет detail-страницы через пул воркеров
  (`career_site_detail_concurrency`, глобально ограничено `detail_slot`).

**Detail extraction (`_scrape_detail_url_to_raw_item`)** на каждый URL берёт
собственный operation-budget (`kind="detail"`, `bypass_max_detail_browser_launches`),
проверяет валидность кандидата (`_is_valid_detail_candidate`: same-site family или
известный ATS-хост + detail-URL сигнатура) и вызывает `_scrape_with_fallback`.
Результат отбрасывается, если нет ни title, ни description; title-only без
description отбрасывается для не-trusted URL (нет доказательства вакансии); не
прошедший freshness — тоже.

### Каскад `_scrape_with_fallback`

```text
httpx GET (client_for_config)
  -> classify (HeuristicFailureSignal): CAPTCHA/CHALLENGE/BLOCKED?
       -> handle_failure (эскалация) -> browser render -> повторная classify
  -> _should_render_detail(): render=true -> browser html
  -> scraper chain: json-ld -> embedded -> nextdata -> dom -> maintext
  -> SPA-shell / title-only -> повтор в браузере (count_first_as_fallback)
```

Chain определяется `_resolve_scraper_chain` (явный `spec.scraper` + fallback, либо
`monitor.scraper_chain`, либо дефолт `json-ld/embedded/nextdata/dom/maintext`;
`xpath` вставляется первым при наличии xpath-конфига).

### Detail protection circuit

Подтверждённый access-control ответ на detail-странице повышает
`detail_protection_failures` и ставит `BLOCKED_NO_BYPASS_LEFT`. По достижении
`career_site_protection_failure_limit` (3) открывается circuit
(`_detail_protection_circuit_open`) — оставшиеся detail-запросы гасятся, чтобы не
молотить защищённый источник.

## Freshness cutoff

`_passes_freshness_cutoff` сравнивает дату поста с `spec.freshness_cutoff_utc`.
Undated элемент проходит, если `freshness_require_date=false` (дефолт), и
считается `freshness_undated_passed`; при `true` — фильтруется. Отсечённые по дате
идут в `freshness_filtered`; когда весь монитор отфильтрован — `FRESHNESS_FILTERED`.

## Таксономия ZeroYieldReason

Нулевой результат всегда объясняется одной причиной — это критично, чтобы
операторы отличали «сайт действительно пуст» от «мы не смогли извлечь».

| Reason | Значение |
|---|---|
| `CONFIRMED_EMPTY` | авторитетный листинг/парсер явно сказал «нет вакансий» |
| `MONITOR_EMPTY` | монитор не дал полезных ссылок, есть следующий монитор |
| `ALL_MONITORS_EXHAUSTED` | цепочка мониторов пройдена без результата |
| `ALL_SCRAPERS_FAILED` | ссылки найдены, но ни одна detail-страница не извлеклась |
| `BLOCKED_NO_BYPASS_LEFT` | подтверждённая защита, bypass-tier'ы исчерпаны |
| `SOFT_404` | «пустая» страница-заглушка вместо листинга |
| `FRESHNESS_FILTERED` | всё найденное отсечено по дате |
| `PRE_DEDUP_ALL_SEEN` | все ссылки уже обработаны в прошлых запусках |
| `BOARD_GONE` | борда/URL больше не существует |

## Teardown

`finally` в `fetch()` всегда: `end_operation(listing token)`, `bypass.close()`
(если есть), `_close_owned_http_clients()` (временные httpx/curl-сессии bypass-tier'ов
+ собственный base client), `domain_intel.save()`. Завершение браузерных
процессов при этом обеспечивается на уровне [browser_lifecycle.md](browser_lifecycle.md),
а не здесь.

## Где смотреть код

- `job_ftch/infrastructure/sources/career_site_source.py`
- `job_ftch/infrastructure/sources/career_monitor_runner.py`
- `job_ftch/infrastructure/sources/career_detail_runner.py`
- `job_ftch/infrastructure/sources/monitor_detector.py`
- `job_ftch/infrastructure/sources/url_scoring.py`

## Связанные документы

- [Ingest stack](ingest_stack.md)
- [Browser lifecycle и teardown](browser_lifecycle.md)
- [Deadlines и concurrency](deadlines_and_concurrency.md)
- [Bypass и escalation path](bypass_and_escalation.md)
- [Source assessment](source_assessment.md)
- [Keyword-search ingest](search_query_ingest.md)
- [Career Site Engines](../entities/career_site_engines.md)
