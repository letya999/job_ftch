---
title: "081 — Operator browser listing probe"
description: "**Status**: ACCEPTED"
updated: 2026-08-19
---
# 081 — Operator browser listing probe

**Status**: ACCEPTED
**Date**: 2026-08-19
**Extends**: [050-browser-session-bypass-protocol.md](050-browser-session-bypass-protocol.md),
[015-ingestion-mode-bypass-strategy.md](015-ingestion-mode-bypass-strategy.md)

## Context

MCP Slice 4 exposed `run_browser_probe` as a structured `not_implemented`
because no application service could open a browser without the adapter
importing Patchright/nodriver clients. Operator live-checks then hung on a
long-lived HTTP MCP server instead of exercising a bounded probe.

Ingest already has a single sanctioned browser entry point:
`infrastructure/sources/browser_utils.open_page` (ADR-050). Operators still
need a source-scoped listing check that is not a full tenant ingest and is
not a persistent headed session.

## Decision

1. Add application port `BrowserSessionProbe` in `application/contracts.py`.
2. Implement listing-only live probe in `infrastructure/browser_probe.py`.
   It must:
   - open the page only through `open_page` + `navigate`;
   - resolve engines via the bypass registry (`patchright_browser` for
     `auto`/`patchright`);
   - apply SSRF checks;
   - use a hard overall deadline and `max_items` cap;
   - default to headless ephemeral context (no persistent profile, no cookie
     capture);
   - return page title, final URL, and bounded same-host link previews.
3. `TenantRunner.probe_browser_listing` is the composition-root call
   (allowed infrastructure import per ADR-039). MCP/Telegram must not import
   browser clients.
4. `run_browser_probe(probe="listing")` executes this port when a listing URL
   exists. Other probes (`detail`, `challenge`, `fingerprint`, `custom_safe`)
   and live session tools stay `not_implemented` until dedicated services exist.
5. Missing extras/binaries return `unavailable` with setup hints, never a hang.

## Consequences

- (+) Operators can verify a career-site listing in a browser without a
  full pipeline run or a daemon MCP HTTP process.
- (+) Browser lifecycle, SSRF, and engine plugins stay in infrastructure.
- (-) Listing extraction is a bounded href preview, not monitor/parser ingest.
- (-) Forced bypass pins, independent engine sweeps, and interactive sessions
  remain out of scope.
