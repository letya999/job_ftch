---
title: "Справочник source stack"
description: "Фактические модули ingestion/source stack: sources, monitors, scrapers, site parsers, assessment и bypass."
updated: 2026-07-28
---
# Справочник source stack

Этот файл фиксирует, какие части source stack реально есть в кодовой базе и за
что они отвечают. Он дополняет overview-документы:
[ingest_stack.md](ingest_stack.md), [source_assessment.md](source_assessment.md)
и [bypass_and_escalation.md](bypass_and_escalation.md).

## Source adapters

Source adapters живут в `job_ftch/infrastructure/sources/` и self-register’ятся
через registry.

| Area | Modules | Responsibility |
|---|---|---|
| local fixtures | `local_fixture.py` | JSON/JSONL fixtures for tests, smoke and eval |
| Telegram | `telegram.py`, `telegram_realtime.py` | channel/group/comments/realtime Telegram ingest |
| career sites | `career_site.py`, `career_site_source.py`, `career_detail_runner.py`, `career_monitor_runner.py` | career-site discovery + detail-page extraction flow |
| declarative | `declarative.py` | declarative extraction for configured career pages |
| composite | `composite.py` | composing several sources into one runtime source |
| realtime | `realtime/rss.py`, `realtime/webhook.py`, `realtime/websocket.py` | realtime/event-driven source variants |
| APIs | `api/greenhouse.py`, `api/lever.py`, `api/hh.py` | direct API source integrations |

Shared helpers in the same package are not source adapters by themselves, but
they are part of the ingest stack:

- `browser_utils`, `http_retry`, `dom_utils`, `embedded_state_utils` — fetch,
  retry and HTML/embedded-state helpers.
- `site_defaults`, `site_fingerprinter`, `structure_detector`,
  `monitor_detector` — site capability and structure detection.
- `raw_item_factory`, `url_scoring`, `site_utils` — RawItem construction,
  URL scoring and shared site utilities.
- `source_deadline`, `shared_limiters`, `source_policy`, `ssrf_guard` —
  deadlines, concurrency/policy helpers and SSRF protection.

## Career-site monitors

Monitors discover vacancy URLs or structured posting payloads before a detail
scraper/parser runs. They live in `job_ftch/infrastructure/sources/monitors/`.

Current monitor families include:

- ATS/API boards: `greenhouse`, `lever`, `ashby`, `workday`,
  `smartrecruiters`, `workable`, `personio`, `recruitee`, `breezy`,
  `softgarden`, `rippling`, `deel`, `eightfold`.
- Discovery helpers: `sitemap`, `rss_board`, `nextdata`, `inline`, `dom`,
  `join`, `api_sniffer`, `api_response_collector`.
- Shared contracts/helpers: `shared.py`.

Monitor output can be a `MonitorResult`, a URL set/list, or structured
`DiscoveredPostingPayload` values depending on the backend.

## Scrapers

Scrapers extract text/structured fields from a page or payload after discovery.
They live in `job_ftch/infrastructure/sources/scrapers/`.

Current scraper styles:

- structured data: `json_ld`, `embedded`, `nextdata`;
- DOM/XPath/main text: `dom`, `xpath`, `maintext`;
- board-specific detail scrapers: `workday`, `workable`, `smartrecruiters`,
  `rippling`.

Scrapers should not decide profile relevance. Their output becomes raw text and
metadata for pipeline nodes.

## Site parsers

Site parsers live in `job_ftch/infrastructure/sources/site_parsers/` and
self-register through `register_site_parser(name, domain_pattern=...)`.

They are domain-specific adapters used when generic/declarative parsing is not
enough. The package currently covers broad job boards, ATS fronts and company
career sites: `hh`, `superjob`, `rabota`, `djinni`, `dou`, `habr`,
`remoteok`, `relocateme`, `workable`, `google`, `microsoft`, `yandex`,
`sber`, `tbank`, `ozon`, `kaspi`, `raiffeisen`, `payme`, and many regional or
company-specific parsers.

When adding a parser:

- implement the `SiteParser` protocol from `site_parsers/base.py`;
- register it in its module;
- keep domain-specific logic in the parser, not in `config.py` or core
  pipeline composition;
- add/update fixtures under `fixtures/real_world/site_parsers/` when possible.

## Source assessment

Source assessment lives in `job_ftch/infrastructure/source_assessment/`.

| Adapter/engine | Responsibility |
|---|---|
| `builtin.py` | registers built-in assessment adapters |
| `career_site_probe.py` | bounded career-site probing helpers |
| `TelegramSourceAssessmentAdapter` | high-confidence incremental Telegram capability/freshness assessment |
| `RSSSourceAssessmentAdapter` | high-confidence feed freshness assessment |
| `KnownSourceAssessmentAdapter` | registry hints from known APIs, monitors and parsers |
| `GenericSourceAssessmentAdapter` | conservative fallback for unknown sources |
| `CareerSiteAssessmentEngine` | probes career-site URL shape, monitor hints, structured metadata and bypass needs |

Assessment answers pre-ingest questions: capability, freshness, confidence,
required browser/bypass and ingest state. It does not fetch final vacancies and
does not replace pipeline relevance.

## Bypass and escalation

Bypass modules live in `job_ftch/infrastructure/bypass/`. They are capability
adapters and policy helpers, not source parsers.

Core runtime pieces:

- `adaptive.py` — adaptive manager, capability inventory and route selection;
- `context.py` — shared bypass context/persona/fingerprint/hardening wiring;
- `attempt_budget.py`, `risk_router.py`, `route_state.py`,
  `transition_policy.py` — budgeting and route-state policy;
- `robots_policy.py`, `preflight.py`, `failure_signal.py` — safety and failure
  classification.

Transport/capability implementations:

- cheap/no-op: `noop`, `curl_bypass`, `managed`;
- browser/session: `stealth_browser`, `camoufox_bypass`, `cloak_bypass`,
  `nodriver_bypass`, `session_handoff`, `session_memory`;
- behavior/fingerprint: `behavior_sim`, `behavioral_noise`, `humanize`,
  `fingerprint_profile`, `fingerprint_generator`, `fingerprint_evolution`,
  `fingerprint_baseline`, `stealth_hardening`;
- advanced/experimental: `persona`, `temporal_graph`, `temporal_shaper`,
  `referrer_chain`, `domain_intel`, `distributed_simulator`,
  `multi_layer_obfuscation`, `physical_context`, `cognitive_state`;
- CAPTCHA: `captcha_models`, `captcha_providers`, `captcha_solver`.

Bypass escalation must be driven by source assessment, failure signals and
runtime policy. It must not be hidden inside a site parser.

## Related docs

- [Source Setup](setup.md) — operator setup and examples.
- [Source Coverage Matrix](coverage_matrix.md) — canonical CIS fixture coverage.
- [Ingest stack](ingest_stack.md) — end-to-end ingestion flow.
- [Career-site runtime flow](career_site_runtime.md) — concrete `fetch()` control flow.
- [Browser lifecycle и teardown](browser_lifecycle.md) — browser session and process teardown.
- [Deadlines и concurrency](deadlines_and_concurrency.md) — time budgets and parallelism.
- [Source assessment](source_assessment.md) — assessment contract and adapters.
- [Bypass and escalation](bypass_and_escalation.md) — bypass policy and strategy.
