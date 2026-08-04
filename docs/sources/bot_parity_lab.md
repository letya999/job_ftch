---
title: "Bot Parity Lab"
description: "Local defensive red-team layer for browser, network, realm, behavior, and identity-parity evidence."
updated: 2026-08-04
---
# Bot Parity Lab

`tools.bot_parity_lab` is a local-only auxiliary verification layer for the
bypass stack. It runs a localhost site, records every request, collects runtime
browser probes, and scores whether a client looks self-consistent across
network, JavaScript, realm, behavior, and short-session history surfaces.

It is not part of production ingest and does not target third-party sites. The
lab is a defensive red-team gate: it should make weak bypass tiers visible
before a scraper route is trusted.

## Scope

The lab covers these reproducible local surfaces:

- Network waterfall, static resources, redirects, pixel, favicon, header order,
  `Accept*`, `Sec-Fetch-*`, `Sec-CH-UA*`, connection reuse, client socket, and
  HTTP protocol version.
- Runtime browser shape: `navigator.webdriver`, UA Client Hints, plugins,
  mime types, `window.chrome`, storage, permissions, media devices, WebGL,
  canvas, audio, fonts, native-function shape, and stack artifacts.
- Cross-realm consistency: window, iframe, classic worker, module worker,
  ServiceWorker, and SharedWorker.
- Behavior and tripwires: pointer and mouse trails, scroll, click, keyboard,
  focus, `navigator.userActivation`, DOM honeypot, local CAPTCHA-style
  challenge request, touch/media/sensor shape, and local identity history.
- Temporal consistency across probes inside the same browser page.

The scorer intentionally keeps a broad catalog. A single browser run currently
evaluates hundreds of atomic signals and records `signal_count` in both raw JSON
and Markdown output.

## Run

```bash
uv run python -m tools.bot_parity_lab.runner --out artifacts/bot_parity_lab
```

The default clients are:

- `httpx_raw`
- `patchright_plain`
- `patchright_browser`
- `nodriver`
- `camoufox`
- `cloak`

`httpx_raw` and `patchright_plain` are negative controls and are allowed to fail
by default. When using the lab as a red-team detector for known weak tiers, pass
an explicit allowlist:

```bash
uv run python -m tools.bot_parity_lab.runner \
  --out artifacts/bot_parity_lab_vendor_clone_final \
  --allow-fail-clients httpx_raw,patchright_plain,patchright_browser,nodriver,camoufox,cloak
```

Outputs:

- `bot_parity_raw.json` contains requests, browser events, findings, and
  `signal_count`.
- `bot_parity_report.md` is the human-readable summary.

## Boundaries

This layer does not claim production bot-management equivalence. Localhost
cannot honestly reproduce external IP reputation, ASN reputation, CDN-side TLS
JA3/JA4, HTTP/2 or HTTP/3 fingerprinting, or vendor-private encrypted challenge
payloads from providers such as Akamai, Kasada, DataDome, or Cloudflare.

The lab records the nearest local equivalents where possible, flags explicit
coverage gaps, and keeps the findings actionable for the project-owned bypass
implementation.

## Quality Gate

Run these focused checks after editing the lab:

```bash
uv run pytest tests/tools/test_bot_parity_lab.py
uv run ruff check tools/bot_parity_lab tests/tools/test_bot_parity_lab.py
uv run ruff format --check tools/bot_parity_lab tests/tools/test_bot_parity_lab.py
```
