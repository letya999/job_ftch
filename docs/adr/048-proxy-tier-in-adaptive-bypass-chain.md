---
title: "048 — Proxy Tier In Adaptive Bypass Chain"
description: "**Status**: SUPERSEDED-BY-074"
updated: 2026-07-24
---
# 048 — Proxy Tier In Adaptive Bypass Chain

**Status**: SUPERSEDED-BY-074
**Date**: 2026-06-30

## Context

После внедрения ADR-022 и ADR-037 стек обхода защиты умел подниматься от
`noop` к `curl_stealth`, затем к `stealth_browser` и `cloak`. На практике этого
оказалось недостаточно для части карьерных сайтов:

1. Для части доменов HTTP и browser fingerprint уже достаточно похожи на
   обычный пользовательский трафик, но блокировка остаётся на уровне IP / ASN /
   rate profile.
2. Browser-backed monitors и site-specific parsers раньше не всегда шли через
   тот же runtime bypass, что и detail scraping. Из-за этого adaptive escalation
   не доходил до browser paths единообразно.
3. На реальных прогонах часть отказов выглядела как `429`, `403`,
   `ERR_CONNECTION_REFUSED` или challenge/captcha body. Эти случаи требуют
   быстрой смены tier, а не многократного повтора на том же IP.

Нужен промежуточный шаг между `stealth_browser` и `cloak`, который дешевле
полного CloakBrowser и решает отдельный класс проблем: смену IP без смены
архитектуры парсинга.

## Decision

1. Канонический adaptive chain становится таким:

   `noop -> curl_stealth -> stealth_browser -> proxy -> cloak`

2. `proxy` реализуется как самостоятельный `BypassStrategy`, регистрируемый
   через registry, без hardcoded ветвления в core.

3. Источники прокси:
   - `config/proxies.yaml`
   - `JOB_FTCH_PROXY_LIST`

   YAML используется как runtime artifact с заранее провалидированными
   прокси, env — как override для ручной подмены.

4. `AdaptiveBypassManager` обязан уметь быстро эскалировать при:
   - `captcha`
   - `blocked`
   - `rate_limit`
   - `timeout`

   Для `captcha` допускается прямой прыжок в сильный browser tier. Для
   `blocked` / `rate_limit` / `timeout` escalation идёт немедленно на
   следующий доступный tier.

5. Runtime bypass инжектится не только в detail scraping, но и в monitor/site
   parser execution path. Rendered DOM monitors и browser-based custom parsers
   должны открывать Playwright pages уже с текущей активной стратегией.

6. `proxy` считается локальным, best-effort tier:
   - он не гарантирует успех;
   - он не заменяет cloak;
   - он должен gracefully деградировать, если пул пустой.

## Consequences

- (+) Появляется отдельный ответ на IP-based блокировки без немедленного
  перехода к самому тяжёлому browser tier.
- (+) Один и тот же adaptive state начинает работать консистентно для monitors,
  site parsers и detail scrapers.
- (+) `config/proxies.yaml` становится воспроизводимым runtime input, который
  можно обновлять отдельным скриптом без правок source configs.
- (-) Увеличивается операционная сложность: нужно поддерживать актуальный и
  живой пул прокси.
- (-) Ошибки transport/browser уровня (`ERR_CONNECTION_REFUSED`, aborted
  navigation) теперь участвуют в логике escalation и требуют тестов на
  классификацию.
- (-) Появляется ещё один optional tier, а значит выше значение корректного
  логирования текущей цепочки и текущей стратегии на run-time.
