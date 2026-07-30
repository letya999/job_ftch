---
title: "050 — Browser Session Bypass Protocol"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 050 — Browser Session Bypass Protocol

**Status**: ACCEPTED
**Date**: 2026-07-04
**Extends**: [015-ingestion-mode-bypass-strategy.md](015-ingestion-mode-bypass-strategy.md),
[037-adaptive-scraping-escalation-policy.md](037-adaptive-scraping-escalation-policy.md),
[048-proxy-tier-in-adaptive-bypass-chain.md](048-proxy-tier-in-adaptive-bypass-chain.md)

## Context

`BypassStrategy` already formalised two browser hooks:

- `apply_browser_args(...)`
- `apply_page(...)`

That contract works for Playwright-backed tiers where `browser_utils.open_page()`
owns the browser lifecycle and the bypass only mutates launch/context/page
state.

The new `camoufox` and `nodriver` tiers do not fit that shape cleanly:

1. They own a different browser runtime and need to open the page themselves.
2. A raw `getattr(bypass, "open_page", None)` check makes the contract implicit
   and invisible to architecture/docs.
3. Monitor/site-parser/browser helper code still needs one shared call site so
   that adaptive escalation stays uniform across scraping paths.

## Decision

1. Keep `BypassStrategy` as the base HTTP/browser mutation protocol.
2. Add a dedicated protocol extension in `application/contracts.py`:

   ```python
   @runtime_checkable
   class BrowserSessionBypass(BypassStrategy, Protocol):
       def open_page(
           self,
           config: dict[str, Any],
           *,
           use_proxy: bool = False,
       ) -> AsyncContextManager[Any]: ...
   ```

3. `infrastructure/sources/browser_utils.py::open_page(...)` becomes the single
   integration point:
   - if the active bypass implements `BrowserSessionBypass`, delegate browser
     lifecycle to it;
   - otherwise keep the existing Playwright-owned path.
4. `camoufox` and `nodriver` must preserve the shared browser config semantics
   that sources already rely on where the backend supports them:
   - `headless`
   - `timeout`
   - `viewport/window size`
   - `locale`
   - `cookies`
   - `persistent_context`
   - `use_proxy`
   - `skip_ssl`
   - `user_agent` when the backend can express it
5. Adaptive bypass remains registry-driven and browser-tier agnostic. Adding a
   new browser engine must not require new `if/elif` dispatch in core code.

## Consequences

- (+) The browser-session escape hatch is explicit, typed, and documented.
- (+) `browser_utils.open_page(...)` stays the only source-side browser entry
  point, so monitor/site-parser/detail scraping paths continue to share one
  adaptive bypass integration seam.
- (+) New non-Playwright browser engines can plug in without smuggling hidden
  methods into the contract.
- (-) Browser-tier implementations now carry more compatibility burden because
  they must map the shared config semantics onto their own runtime.
- (-) `BrowserSessionBypass` broadens the contract surface and therefore needs
  dedicated tests for config propagation.
