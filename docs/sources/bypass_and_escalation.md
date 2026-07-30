---
title: "Bypass и escalation path"
description: "Adaptive bypass для ingest: failure signals, route axes, proxy/session/challenge boundaries и запреты."
updated: 2026-07-28
---
# Bypass и escalation path

Bypass в `job_ftch` — это infrastructure capability для чтения источников,
которые могут блокировать обычный HTTP/browser path. Он не является scraper-ом
и не принимает продуктовых решений. Его задача — подобрать допустимый маршрут
доступа к странице или API и вернуть управление monitor/scraper/site_parser.

## Почему это отдельный слой

Career sites ломаются не только из-за HTML. Частые причины:

- TLS/HTTP fingerprint;
- Chromium/browser fingerprint;
- rate limit;
- IP/ASN блокировка;
- session/cookie requirement;
- JS rendering requirement;
- challenge/CAPTCHA;
- unstable API or embedded state.

Если всё это спрятать в scraper, scraper начнёт одновременно отвечать за
network, browser, parsing, retries и product behavior. Поэтому bypass держится
отдельно и подключается как capability.

## Оси route state

`bypass="auto"` не должен восприниматься как одна лестница. Runtime route
состоит из независимых осей:

- transport: plain HTTP, curl impersonation, browser-backed fetch;
- browser: стандартный browser, stealth browser, альтернативный browser runtime;
- network: direct или proxy;
- session: чистая сессия, сохранённая сессия, handoff;
- challenge: wait, checkbox/challenge handling, solver-compatible path.

Классифицированный failure signal выбирает ось. Например, TLS signal ведёт к
transport change, а IP/rate-limit signal сначала должен менять network path.

## Default fallback

Для неизвестного сигнала применяется консервативный fallback:

```text
noop -> curl_stealth -> stealth_browser -> nodriver -> camoufox -> cloak
```

Это fallback, а не продуктовая стратегия. Если сигнал классифицирован,
runtime должен идти в подходящую capability напрямую.

## Proxy

Proxy — это network capability, а не “ступень между браузерами”. Он нужен для
IP/ASN/rate-limit случаев и должен иметь отдельные бюджеты.

Источники proxy:

- `config/proxies.yaml`;
- `JOB_FTCH_PROXY_LIST`;
- provider-specific runtime config.

Proxy path не должен скрывать parser failures. Если HTML изменился, это parser
issue, а не повод бесконечно менять IP.

## Browser/session/challenge boundaries

Playwright-compatible tiers используют контракты вроде `apply_browser_args` и
`apply_page`. Tiers, которые сами владеют browser runtime, должны иметь явное
расширение вроде `BrowserSessionBypass.open_page(...)`.

Session handoff и challenge handling — bounded операции над текущим route.
Они не должны превращаться в постоянный daemon, бесконечный worker или скрытую
оркестрацию.

## Бюджеты и остановка

Bypass обязан уважать:

- source soft/hard deadlines;
- per-operation route attempts;
- browser launch budgets;
- proxy rotation budgets;
- weighted work budget per source;
- global source concurrency.

Если route исчерпал бюджет, источник должен получить явный degraded/failed
outcome, а не пустой успешный результат.

## Что запрещено

- принимать relevance decision внутри bypass;
- парсить вакансии в bypass strategy;
- бесконечно перезапускать browser/session/challenge path;
- маскировать blocking как “нет вакансий”;
- держать отдельный hardcoded catalog сайтов внутри bypass;
- обходить robots/legal policy, если runtime настроен на enforcement.

## Где смотреть код и решения

- `job_ftch/infrastructure/bypass/`
- `job_ftch/infrastructure/sources/browser_utils.py`
- `job_ftch/infrastructure/sources/source_policy.py`
- `job_ftch/infrastructure/sources/source_deadline.py`
- [BypassStrategy](../entities/bypass_strategy.md)
- ADR-047, ADR-048, ADR-050, ADR-072, ADR-074, ADR-077

## Связанные документы

- [Ingest stack](ingest_stack.md)
- [Справочник source stack](source_stack_reference.md)
- [Career-site runtime flow](career_site_runtime.md)
- [Browser lifecycle и teardown](browser_lifecycle.md)
- [Deadlines и concurrency](deadlines_and_concurrency.md)
- [Career Site Engines](../entities/career_site_engines.md)
- [Source assessment](source_assessment.md)
