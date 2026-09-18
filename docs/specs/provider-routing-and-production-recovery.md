---
title: "Provider routing and production recovery"
description: "Requirements for node-scoped LLM routing, CAPTCHA-only CLIProxyAPI, truthful Telegram delivery and source recovery."
updated: 2026-09-18
---
# Provider routing and production recovery

**Decision**: [ADR 100](../adr/100-node-scoped-llm-provider-routing.md)  
**Related decisions**: [ADR 090](../adr/090-delivery-truth.md), [ADR 097](../adr/097-proxy-captcha-budget.md), [ADR 098](../adr/098-source-coverage-observability.md)  
**Plan**: [Implementation plan](../plans/provider-routing-and-production-recovery.md)

## Goal

Make provider/model selection explicit for each LLM-consuming graph node,
restrict CLIProxyAPI to image CAPTCHA recognition, keep the application alive
during provider degradation, report actionable failures to Telegram and
OpenObserve, restore truthful publication accounting, and repair failing source
routes using phase-level evidence.

## Scope

### Provider and model routing

- Named provider profiles contain endpoint, credential reference, timeout,
  retry, capability and session-storage configuration.
- Graph nodes select a provider profile and model independently.
- The production defaults use the OpenAI profile for ETL extraction,
  relevance and ontology compilation.
- CLIProxyAPI is assigned to `cliproxy_image` with
  `gemini-3.8-flash-high`; it is not a main-ETL default.
- Configuration files contain references to secrets, never secret values.
- Existing global gateway settings are migrated with explicit warnings; no
  compatibility path may silently change a model.

### Degradation and fallback

- Provider unavailability does not terminate the application process.
- Syntax errors, missing references and impossible capability bindings remain
  startup configuration errors.
- Network, authentication, quota and model-catalog states affect readiness and
  per-run capability.
- Scheduler preflight checks only bindings required by the resolved graph.
- Source ingest does not start when a required binding is unavailable and no
  authorized fallback exists.
- Fallback chains are explicit per binding. Every switch is persisted and
  reported; absence of a fallback means no switch.
- OpenAI quota exhaustion receives two retries at 30-minute intervals, then
  blocks the run without further provider calls.
- Authentication failure and unknown model are reported immediately without
  repeated calls.

### Telegram operator notification

Notifications distinguish:

- provider unavailable;
- authentication failed;
- quota exhausted;
- model unavailable;
- fallback selected;
- run skipped before ingest;
- run stopped after bounded retries;
- publication partially or fully failed.

Repeated identical incidents are deduplicated by tenant, binding and reason;
recovery produces one resolution notification.

### Confirmed publication

- A rejected card is not a successful sender result.
- `sent` and delivery ledgers update only after a Telegram API success response.
- Telegram receipts retain `chat_id`, `message_id` and confirmation time.
- Publication metrics expose candidates, policy rejections, already-published
  entries, card validation rejections, attempted sends, confirmed deliveries,
  transient failures, fatal failures and pending work.
- Bot readiness uses `getMe`, `getChat` and bot membership/rights checks without
  sending a test message.
- Repair of historical false-positive ledger entries is a separate dry-run and
  approved operation.

### LLM observability

Each call records tenant, run, graph node, requested and resolved provider,
requested and resolved model, operation, attempt, timeout, queue wait, request
and total latency, token counts, status, provider request ID and normalized
retry/error reason. Prompts, responses, credentials and authorization headers
remain excluded.

Retry reasons distinguish network, provider 5xx, rate limit, quota, auth,
unknown model, structured-output validation, client validation and run-budget
cancellation.

### Source observability and budgets

- `jf_source_run_stats.duration_ms` records real source duration.
- Discovery and detail phases have separate timestamps, durations and budgets.
- Monitor and scraper attempts retain adapter name, attempt number, duration,
  result, HTTP status or normalized exception, discovered URL count and
  remaining source budget.
- Browser-pool wait, proxy wait, CAPTCHA wait, rate-limit sleep and navigation
  count are independently observable.
- Primary causes are retained when aggregate outcomes such as
  `all_monitors_exhausted` or `all_scrapers_failed` are produced.
- Discovery cannot consume the detail reserve. Rich listing payloads bypass
  unnecessary detail requests; detail work begins incrementally where the
  source contract permits it.
- Work found before a deadline remains recoverable rather than forcing listing
  discovery to restart from zero.

## Source recovery groups

### Partial listing with exhausted detail budget

Priority sources: EPAM Kazakhstan, Tele2 Kazakhstan, Tabby, Avito, Techvill,
HH RU and HH KZ.

For each source, establish discovery duration, detail start time, candidate
count, detail concurrency, remaining budget and whether listing payloads were
already sufficient. EPAM's dedicated JSON route must be preferred when its
contract remains valid.

### Listing discovery failure

Priority order: Hirify, X5, Rostelecom, Tochka, Sber, VK, Wellfound, Helio and
DataArt.

Hirify must expose whether its dedicated parser was selected, whether its
source-specific deadline was applied, and any application-level rate limit.
The original typed cause must survive generic fallback exhaustion.

### Detail extraction failure

Sources: Documentolog, Getmatch, JSeek, Ucell and JetBrains.

Each repair starts from recorded listing URLs and per-scraper results. A new
parser is added only when the existing registered parser or declarative route
cannot represent the current public source contract.

### Policy outcomes

Sources: AI Engineer Jobs, Habr variants, Geekjob variants and LinkedIn
variants.

Policy outcomes identify the exact rule and distinguish expected legal/policy
skips from configuration or classification errors. Habr, Geekjob and AI
Engineer Jobs are checked for false-positive policy classification and current
public API, RSS or structured endpoints. LinkedIn remains on an authorized API,
feed, export or other permitted route; technical bypass is not an acceptance
criterion.

### Hard deadlines and WAF

Kaspersky, Semrush, Alfa, Kontur and staff.am require phase timing before any
deadline increase. PT Security and jobs.dou.ua require challenge classification
and an authorized bounded route. Image OCR is not treated as a solution for
Turnstile, reCAPTCHA, hCaptcha, JavaScript WAF or IP/ASN blocking.

## Status contract

Source outcomes expose independent fields:

```text
phase: assessment | listing | detail | pipeline | publish
outcome: success | partial | confirmed_empty | failed | skipped
reason: policy_expected | policy_misconfigured | deadline | rate_limit |
        challenge | parser_gap | stale_url | auth | transport |
        schema_changed | monitor_exhausted | scraper_exhausted
```

Legacy `status` remains during migration and is derived from the new fields.

## Acceptance criteria

1. CLIProxyAPI can be unreachable while the bot, scheduler, API and health
   endpoints remain live; readiness identifies the affected binding.
2. A graph requiring only `openai_main` can run while `cliproxy_captcha` is
   unavailable.
3. A run requiring an unavailable binding is stopped before source ingest and
   produces one deduplicated Telegram incident notification.
4. A configured fallback selects only the declared provider/model and records
   both requested and resolved bindings; an empty fallback list never switches.
5. Production CAPTCHA vision resolves to `cliproxy_captcha` and
   `gemini-3.8-flash-high`; extraction, relevance and ontology resolve to their
   configured OpenAI bindings.
6. Quota exhaustion causes no more than two retries separated by 30 minutes;
   auth and unknown-model failures are not retried.
7. A card rejected by validation produces `sent=0` and no delivery-ledger
   update. A successful Telegram response stores its message receipt.
8. Source rows have non-zero measured duration when work occurred and expose
   discovery/detail timing plus every attempted monitor/scraper.
9. Partial sources retain discovered work and cannot spend the reserved detail
   budget on another discovery retry.
10. Every source named in this specification has a fixture-backed diagnosis,
    a repaired route or an explicit expected-policy outcome.
11. OpenObserve can group latency and failures by graph node, provider, model,
    source phase and normalized reason without payload or secret capture.
12. Existing architecture, graph, YAML, observability and production-like
    Compose gates pass.

## Compatibility and safety

- Existing env names remain readable for one migration window and emit a
  deprecation warning.
- No production credential is copied into YAML, logs, fixtures or tests.
- No live CAPTCHA solve, paid proxy probe, Telegram test post, ledger repair,
  migration or deployment occurs without separate approval.
- LinkedIn recovery must use an authorized integration path.

## Open questions

- Exact OpenAI extraction, relevance and ontology model IDs must be approved
  before implementation changes production defaults.
- The Telegram operator target for infrastructure incidents may reuse the
  existing admin chat or require a dedicated target; the implementation must
  not guess silently.
- Historical false delivery ledger entries require a reviewed dry-run report
  before any correction.
