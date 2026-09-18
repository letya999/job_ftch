---
title: "Provider routing and production recovery — implementation plan"
description: "Ordered implementation stages for provider routing, delivery truth, observability and source recovery."
updated: 2026-09-18
---
# Provider routing and production recovery — implementation plan

## Delivery strategy

Use one feature branch and a sequence of reviewable commits. No production
state change belongs to an implementation commit. Provider routing and
observability land before source-specific tuning so later fixes are based on
measured phase data.

## Stage 0 — Baseline and regression fixtures

**Purpose:** preserve the observed failures before changing behavior.

- Add tests reproducing global gateway/model coupling.
- Add a publisher test where card validation returns no rendered card but the
  sender currently reports success.
- Add source-run tests showing zero duration and lost monitor/scraper causes.
- Capture redacted fixtures for the named critical sources through the existing
  fixture process; do not store cookies, tokens or personal payloads.
- Record current graph hash and resolved provider/model bindings in the test
  baseline.

**Exit gate:** every root problem has a failing local test or a documented
fixture gap requiring a bounded live probe.

## Stage 1 — Named provider profiles

**Purpose:** separate transport, credentials, model policy and capability.

- Add typed named provider profiles to settings/runtime loading.
- Reuse the existing LLM registry to construct one instance per referenced
  profile.
- Add capability validation for text, structured output and vision.
- Add graph params for provider and model bindings at LLM-consuming nodes.
- Resolve defaults in the composition root and expose the resolved graph.
- Keep legacy settings as a warning-producing compatibility adapter only.
- Remove model overrides from gateway activation.

**Tests:** config parsing, unknown profile, missing capability, independent
provider/model selection, legacy migration and secret redaction.

**Exit gate:** changing a provider never changes a model in resolved config.

## Stage 2 — Graceful readiness and run preflight

**Purpose:** keep the service alive while preventing wasteful runs.

- Split liveness, readiness and graph-run capability.
- Probe only referenced providers and models.
- Classify auth, quota, unknown model, rate limit, transport and timeout.
- Implement explicit ordered fallbacks and switch events.
- Add the two-attempt, 30-minute quota policy using durable scheduler state.
- Deduplicate incident notifications and emit one recovery notification.
- Stop an incapable run before any source fetch begins.

**Tests:** optional provider unavailable with healthy process; required provider
unavailable; authorized fallback; no fallback; quota schedule; restart during
retry window; auth/model failure without retry.

**Exit gate:** provider outages cannot crash the service or start an incapable
ingest.

## Stage 3 — CAPTCHA-only CLIProxyAPI

**Purpose:** apply CLIProxyAPI to the requested narrow capability.

- Define `cliproxy_captcha` with an independent base URL, credential reference
  and mounted session directory.
- Bind `cliproxy_image` to `gemini-3.8-flash-high`.
- Keep browser wait and CapSolver routes explicit.
- Validate model-catalog presence as readiness, not liveness.
- Expose session/auth age and last upstream auth error without exposing files.
- Update dev/prod examples and VPS operations documentation.

**Tests:** vision-only capability, missing model, unavailable sidecar, expired
auth, route ordering and no mutation of ETL bindings.

**Exit gate:** disabling CLIProxy affects image CAPTCHA readiness only.

## Stage 4 — Confirmed Telegram delivery

**Purpose:** make `sent` equal confirmed Telegram messages.

- Replace the void sender result with delivered/rejected/retryable/fatal
  outcomes and receipts.
- Treat card validation rejection as rejected, not delivered.
- Persist `chat_id`, `message_id` and confirmation time after API success.
- Update ledger only after confirmed delivery.
- Add `getMe`, `getChat` and membership/rights readiness checks.
- Emit the complete publication conservation counters.
- Prepare, but do not execute, a dry-run report for historical false ledger
  entries.

**Tests:** rejected render, successful receipt, flood wait, forbidden target,
unknown outcome and crash boundaries.

**Exit gate:** `sent == count(delivery receipts)` for every run.

## Stage 5 — OpenObserve contracts

**Purpose:** make provider and source latency actionable.

- Enrich LLM events with requested/resolved binding, node, attempt and
  normalized retry cause.
- Add connect, queue, request and total latency dimensions.
- Record provider transitions and incident lifecycle.
- Record source phase and adapter-attempt spans.
- Add dashboards for provider readiness, fallback, LLM p50/p95/p99, Telegram
  delivery conservation and source phase budgets.
- Keep business truth in PostgreSQL and payloads out of telemetry.

**Exit gate:** one dashboard query explains where a failed call/source spent
its time and why it terminated.

## Stage 6 — Source timing and recoverable detail work

**Purpose:** prevent listing discovery from starving detail extraction.

- Persist real source start, finish and duration.
- Divide source budget into discovery, detail reserve and cleanup margin.
- Prevent retries whose timeout exceeds the phase's remaining budget.
- Start bounded detail work incrementally where supported.
- Emit rich listing payloads without redundant detail fetch.
- Persist discovered work for safe continuation under the existing durable
  ingest mechanism.

**Initial verification set:** EPAM Kazakhstan, Tele2 Kazakhstan, Tabby, Avito,
Techvill, HH RU and HH KZ.

**Exit gate:** discovered candidates remain attributable and recoverable when a
detail phase reaches its deadline.

## Stage 7 — Critical source repairs

Work in this order, one fixture-backed source family per commit:

1. Hirify dedicated parser selection, rate limit and deadline override.
2. Geekjob, Habr and AI Engineer Jobs policy classification.
3. EPAM and Tele2 Kazakhstan detail routing.
4. HH RU/KZ partial completion.
5. X5, Rostelecom, Tochka, Sber, VK, Wellfound, Helio and DataArt discovery.
6. Documentolog, Getmatch, JSeek, Ucell and JetBrains detail extraction.
7. Kaspersky, Semrush, Alfa, Kontur and staff.am phase-specific deadlines.
8. PT Security and jobs.dou.ua authorized challenge routes.
9. LinkedIn authorized API/feed/export route or explicit expected-policy state.

For every source:

- reproduce;
- identify the first failed phase and adapter;
- repair the existing registered route when possible;
- add a redacted fixture and one focused test;
- verify terminal cause preservation;
- record the expected production outcome.

**Exit gate:** every named source is successful, explicitly partial with
recoverable work, or intentionally skipped by a named policy rule.

## Stage 8 — Integration and release preparation

- Run architecture, graph, YAML, source, publisher and observability tests.
- Run the documented local quality gate.
- Run repo safety scan before commit and prepush before push.
- Open a PR to `dev` and run all CI workflows.
- After approval, deploy a bounded canary with provider switching disabled
  unless explicitly configured.
- Verify provider bindings, Telegram receipts, source phase metrics and
  OpenObserve dashboards before a full scheduled run.
- Production ledger repair, full ingest and deployment remain separate approved
  operations.

## Commit sequence

1. `docs: specify node-scoped provider recovery`
2. `feat: add named llm provider profiles`
3. `feat: add graph binding preflight and fallback`
4. `feat: route captcha vision through cliproxy`
5. `fix: record confirmed telegram delivery`
6. `feat: expose provider and source phase telemetry`
7. `fix: preserve source detail budgets`
8. Source-family fixes, one conventional commit per family.

## Definition of done

- All acceptance criteria in the linked specification pass.
- No secret or production payload appears in Git history or telemetry fixtures.
- No provider/model switch can occur without resolved configuration and an
  observable event.
- No Telegram ledger entry exists without a confirmed delivery receipt.
- Every degraded source has a phase, primary cause, attempt trail and ownerable
  next action.
