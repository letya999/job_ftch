---
title: "037 — Adaptive Scraping Escalation Policy (registry-driven, no host hardcode)"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 037 — Adaptive Scraping Escalation Policy (registry-driven, no host hardcode)

**Status**: ACCEPTED
**Date**: 2026-06-18
**Extends**: [022-cloakbrowser-advanced-bypass.md](022-cloakbrowser-advanced-bypass.md),
[023-adaptive-scraping-escalation.md](023-adaptive-scraping-escalation.md),
[015-ingestion-mode-bypass-strategy.md](015-ingestion-mode-bypass-strategy.md)
**Closes**: ADR-023 status (`PROPOSED` → `ACCEPTED` via this ADR).

## Context

`AdaptiveBypassManager` (`infrastructure/bypass/adaptive.py`) already ships with
`TIERS = ['noop', 'curl_stealth', 'stealth_browser', 'cloak']` and an
`escalate()` method. The wiring is in
`infrastructure/sources/career_site_source.py:66-95` (`_try_escalate_bypass`).
But three problems remain:

1. **The escalation contract is implicit.** "When should I escalate?" lives in
   code that catches `Exception` and increments a counter with no TTL or window.
   Two near-simultaneous failures look the same as two failures a week apart.
2. **No failure classification.** A Cloudflare 403, a DataDome 503, a 5xx
   timeout, and a parser returning zero items are all the same exception. The
   right response differs: 403 → escalate, 5xx → retry same tier with backoff,
   zero items → re-try with `monitor: auto` (ADR-025 territory).
3. **Hardcoded `cloak` tier dependency.** ADR-022 documents CloakBrowser as the
   ultimate tier; the code unconditionally puts `'cloak'` in the tier list.
   If `cloakbrowser` is not installed (no `[stealth]` extra), the registry
   import fails at `load_extensions()` time. There is no graceful skip for
   dev / CI environments.

ADR-023 listed all three axes (tiered bypass, auto-sniffer, strategy caching) but
stayed `PROPOSED` and stopped at the design discussion. The implementation
`adaptive.py` only covers the first axis. The other two belong to other ADRs
(025, 026) and are out of scope here.

## Decision

1. New `FailureSignal` Protocol in `application/contracts.py`:
   ```python
   class FailureSignal(Protocol):
       def classify(
           self, *, status_code: int | None, body: bytes | None, error: BaseException | None
       ) -> FailureKind: ...
   ```
   `FailureKind ∈ {"ok", "rate_limit", "captcha", "blocked", "timeout", "parse_empty", "unknown"}`.
2. Default implementation `HeuristicFailureSignal` (in
   `infrastructure/bypass/failure_signal.py`) maps:
   - 401/403 → `blocked`
   - 429 → `rate_limit`
   - 5xx → `timeout`
   - reCAPTCHA / Cloudflare / DataDome / Kasada strings in body → `captcha`
   - Zero extracted items from a non-empty page → `parse_empty`
   - everything else → `unknown`
3. `AdaptiveBypassManager` gains:
   - `_failure_window: dict[SourceId, RingBuffer[(ts, strategy, FailureKind)]]`
     with TTL 1 hour.
   - `escalate_if_needed(source_id, signal) -> Strategy | None`:
     - Window of last 30 min, ≥ 3 failures of `kind in {blocked, captcha, timeout}`
       on the current strategy → call `escalate()`.
     - 1 `captcha` failure is enough to escalate (CAPTCHA is binary).
   - `handle_failure(source_id, *, status_code, body, error)` — single entry
     point used by `career_site_source.py` instead of bare `except Exception`.
4. The tier list is **built dynamically** from registered bypasses at construction:
   `tiers = [name for name in DEFAULT_TIER_ORDER if resolve_bypass(name) is not None]`.
   If `cloakbrowser` is not installed, `cloak` is silently absent. No import error.
   `DEFAULT_TIER_ORDER` is a const in `bypass/__init__.py`, not a hardcoded
   `if/elif` in `adaptive.py`.
5. `JOB_FTCH_ADAPTIVE_ENABLED` (default `true` in dev, `false` in CI) gates the
   whole manager: when disabled, `career_site_source` always uses the explicit
   `bypass` from YAML.
6. New Prometheus counter `bypass_escalations_total{source, from_tier, to_tier}`.
7. `career_site_source.py:_try_escalate_bypass` is rewritten to call
   `await self.bypass_strategy.handle_failure(source_id, status_code=..., body=..., error=...)`
   when an extraction failure is observed.

## Consequences

- (+) Escalation is data-driven: a `captcha` triggers an immediate bump;
  `parse_empty` does not, because re-trying with a heavier browser does not
  fix an empty page.
- (+) `cloak` and any other optional tier are drop-in via registry; dev/CI
  without extras keeps working.
- (+) The contract between sources and the bypass layer is explicit:
  `handle_failure` instead of bare `except`. Documented in `BypassStrategy`
  Protocol.
- (+) ADR-023 finally gets `ACCEPTED`. ADR-022 keeps its `ACCEPTED` status and
  is now a concrete instance of the more general policy.
- (-) Two new files (`failure_signal.py`, plus the contract addition). Test
  surface for `AdaptiveBypassManager` grows.
- (-) `career_site_source` loses the bare-`except` style. The diff is large but
  localised to `_try_escalate_bypass` and `_fetch_with_bypass`.
- (-) Operators who relied on `cloak` always being last will see it gone from
  the list when the extra is missing. Mitigated by the dev/CI gate and the
  startup log that prints the active tier list.
