---
title: "083 — Operator sessions, challenge probes, captcha wait, parser pin"
description: "**Status**: ACCEPTED"
updated: 2026-08-19
---
# 083 — Operator sessions, challenge probes, captcha wait, parser pin

**Status**: ACCEPTED
**Date**: 2026-08-19
**Extends**: [081-operator-browser-listing-probe.md](081-operator-browser-listing-probe.md),
[082-operator-bypass-pin-and-sweep.md](082-operator-bypass-pin-and-sweep.md),
[050-browser-session-bypass-protocol.md](050-browser-session-bypass-protocol.md)

## Context

MCP could pin a bypass and open one listing page, but operators still could
not open a short-lived headed session, inspect a detail/challenge page, wait
out a captcha, or pin a monitor/scraper for one ingest. Interactive work then
either hung on HTTP `:8765` or bounced as `not_implemented`.

## Decision

1. Extend the listing probe to `detail` and `challenge`. Both stay one
   ephemeral `open_page` / `navigate` call with a hard deadline. `fingerprint`
   and `custom_safe` stay `not_implemented`.
2. Challenge probe classifies via the existing challenge classifier plus
   `observed_challenge_type`. Optional `solve=browser_wait|provider` uses
   `CaptchaSolverBypass`. Paid/provider solve still requires
   `captcha_authorized_domains`. Responses never include tokens, cookies
   values, or API keys.
3. Live sessions live in an in-process registry owned by `TenantRunner`.
   Defaults: ephemeral profile, TTL 180s, max 2 sessions. `persistent` /
   `domain` profiles stay `unsupported`. Commands: `wait`, `reload`,
   `wait_challenge`, `solve`, same-origin `navigate`. Artifacts: `text`,
   truncated `html`, cookie *names*, screenshot path. `trace` stays
   `not_implemented`.
4. `TenantRunner.run_tenant` accepts `parser_override`. Career-site pins
   registered `monitor` and/or `scraper` names. `declarative_html` pins
   `parser_kind`. `browser` pins `parser`. Other source kinds stay
   `unsupported`.
5. MCP still does not import browser clients. `TenantRunner.close` closes
   leftover sessions before descendant teardown.

## Consequences

- (+) Operator can inspect listing, detail, and challenge, wait or solve
  captcha under existing gates, keep a short session, and pin a parser.
- (-) Sessions are process-local and expire. They are not a headed daemon.
- (-) Site parsers remain URL-bound; career-site pin is monitor/scraper.
- Follow-up: [084](084-operator-persistent-fingerprint-parser-override.md) implements
  persistent/domain profiles, fingerprint/custom_safe, trace, pollable captcha, and
  an explicit mismatched-host site-parser pin.
