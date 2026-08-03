---
title: "Bypass и escalation path"
description: "Adaptive bypass для ingest: failure signals, route axes, proxy/session/challenge boundaries и запреты."
updated: 2026-08-03
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

- transport: plain HTTP, `curl_cffi` impersonation, `tls-client` impersonation,
  browser-backed fetch;
- browser: стандартный browser, stealth browser, Patchright-backed browser,
  альтернативный browser runtime;
- network: direct или proxy;
- session: чистая сессия, сохранённая сессия, handoff;
- challenge: wait, checkbox/challenge handling, solver-compatible path.

Классифицированный failure signal выбирает ось. Например, TLS signal ведёт к
transport change, а IP/rate-limit signal сначала должен менять network path.

## Default fallback

Для неизвестного сигнала применяется консервативный fallback:

```text
noop -> curl_stealth -> tls_client -> stealth_browser -> patchright_browser -> nodriver -> camoufox -> cloak
```

Это fallback, а не продуктовая стратегия. Если сигнал классифицирован,
runtime должен идти в подходящую capability напрямую.

Текущая матрица основных tiers:

| Tier | Назначение |
|---|---|
| `curl_stealth` | Дешёвый HTTP transport с TLS/HTTP impersonation через `curl_cffi`. |
| `tls_client` | Альтернативный HTTP transport с domain-sticky TLS client identifier через `tls-client`. |
| `stealth_browser` | Playwright-compatible browser tier для базового JS/rendering. |
| `patchright_browser` | Явный Patchright tier для fingerprint/generic challenge сигналов без перехода сразу к session-owning браузерам. |
| `nodriver` | Session-owning CDP tier для Cloudflare/browser challenge path, с ADR-073 license gate. |
| `camoufox` | Firefox anti-detect tier для тяжёлых fingerprint случаев. |
| `cloak` | Last-resort patched Chromium tier. |

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

Challenge detection is unified through
`job_ftch.infrastructure.bypass.challenge_classifier`:

- HTTP/monitor/browser surfaces emit `challenge_detected` telemetry with
  `domain`, `type`, `confidence`, `surface`, `latency_ms` and an evidence hash;
- telemetry never includes provider tokens or cookie values;
- browser response listeners classify status/headers early and set the observed
  challenge type for the current bypass controller;
- DOM monitor rejects challenge HTML before link extraction, so a protected page
  is not misreported as an empty job board.

## Qrator / jsid

Qrator обрабатывается как отдельный failure signal:

- headers/body markers (`X-Qrator-*`, `Qrator`, `jsid`, short JS reload shell)
  классифицируются как `qrator_challenge`;
- transition policy сначала активирует residential/sticky network route, затем
  browser fallback при наличии бюджета;
- generic datacenter proxy route для таких сигналов не считается достаточным.

В проекте намеренно нет hardcoded reverse-engineering solver-а для `jsid` и нет
приватных Qrator API. Для owned/authorized targets это должен быть явный
manual/provider integration path с отдельным security/legal review.

## Warmed browser profile

Persistent browser context по умолчанию остаётся source-scoped temp profile.
Для контролируемого “прогретого” профиля задайте:

```text
JOB_FTCH_BROWSER_PROFILE_DIR=.runtime/browser_profiles/<source-or-tenant>
JOB_FTCH_BROWSER_PROFILE_PERSISTENT=true
```

Профиль должен жить под ignored `.runtime/`, не должен быть личным daily
профилем оператора и не должен попадать в git.

For storage-state style reuse, keep it opt-in and source-scoped:

```text
JOB_FTCH_BROWSER_SESSION_STATE_ENABLED=true
JOB_FTCH_BROWSER_SESSION_STATE_DIR=.runtime/session_states
```

Only allowlisted clearance cookies and the matching user-agent are persisted.
Values are never logged.

## Explicit Non-Goals

The project does not implement reCAPTCHA submit/XHR token substitution,
Cloudflare checkbox automation, `cf_clearance` harvesting, or private WAF solver
integrations as generic scraper behavior. Those paths are limited to
owned/explicitly authorized test targets and must go through a separate
security/legal review before any implementation.

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
