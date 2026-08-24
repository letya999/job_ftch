---
title: "084 — Operator persistent/domain sessions, fingerprint probes, trace, extendable captcha, parser host override"
description: "**Status**: ACCEPTED"
updated: 2026-08-19
---
# 084 — Operator persistent/domain sessions, fingerprint probes, trace, extendable captcha, parser host override

**Status**: ACCEPTED
**Date**: 2026-08-19
**Extends**: [083-operator-sessions-challenge-parser-pin.md](083-operator-sessions-challenge-parser-pin.md),
[081-operator-browser-listing-probe.md](081-operator-browser-listing-probe.md),
[033-plugin-based-domain-parsers.md](033-plugin-based-domain-parsers.md)

## Context

Slice 7 left `fingerprint` / `custom_safe` probes, `trace` artifacts,
persistent/domain profiles, and URL-parser pins on a mismatched host as
`not_implemented` / `unsupported`. Headed captcha could not be kept open
without blocking an MCP tool call (the `:8765` hang mode).

## Decision

1. `run_browser_probe(probe="fingerprint")` classifies the URL with the
   existing HTTP fingerprinter and may add a one-page UA snapshot. Cookie
   values and unsafe `detected_config` keys stay redacted.
2. `custom_safe` is a listing-style one-page probe: no clicks, no forms, no
   cookie values, bounded link previews.
3. Operator sessions accept `profile=persistent|domain`. They reuse sanctioned
   `open_page` persistent context under `browser_profile_root/operator/<key>`.
   Responses expose `profile` + `profile_key`, never the filesystem path or
   cookie values.
4. `capture_browser_artifact(trace)` writes a Playwright zip when tracing is
   available, otherwise a navigation JSONL. Cookie values are not included.
5. Headed/manual captcha wait is pollable: `wait_challenge` returns within the
   command deadline and extends TTL; `extend` keeps the session without an
   infinite tool call.
6. An explicit `run_source(parser=<site parser>)` pin is allowed even when the
   source host does not match `domain_pattern`. Default ingest stays URL-bound.
   The operator payload sets `parser_host_mismatch` and a warning note.

## Consequences

- (+) Operator probes and sessions no longer bounce those names.
- (+) Captcha wait cannot hang the MCP process.
- (-) Persistent profiles are process-local directories, not a headed daemon.
- (-) A mismatched-host parser pin is an override; the parser may still refuse
  the page internally.
