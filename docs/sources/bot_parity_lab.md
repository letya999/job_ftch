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

The same `tools/bot_parity_lab` tool also contains the full owned
`bot-browser-parity-lab` reference implementation under its local `paritylab`
package. It adds passive TLS ClientHello capture, JA3/JA4 evidence,
HTTP/2/HTTP/3-aware request graph scoring, window/iframe/worker/SharedWorker
probes, opaque-payload observations, an opt-in protected career-site playground
with local challenge/clearance/gate decisions, and the explainable
`hard_bot_signal` / `medium_suspicious` / `low_entropy_mismatch` finding model.
Keep its `LICENSE` and `NOTICE.md` with the vendored copy.

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

Fast in-repo smoke lab:

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

Full parity campaign against the real `job_ftch` browser tiers:

```bash
uv run python scripts/eval/run_bot_browser_parity_lab.py \
  --tiers patchright_browser,nodriver,camoufox,cloak \
  --out artifacts/bot_parity_lab
```

The wrapper sets `PARITYLAB_CLIENT_HOOK=examples.job_ftch_hook:run_owned_browser`
and runs the vendored `project-browser-hook` adapter against each selected tier.
The hook uses the actual `job_ftch` bypass strategy, browser config preparation,
`open_page`, and `navigate` flow against a loopback-only URL. A gated run
returns a non-zero exit code when the selected browser tier emits hard or medium
parity findings. Use `--allow-fail-tiers` only for known negative controls or
while triaging a tier.

Parsed `raw.json` artifacts can be normalized through
`job_ftch.infrastructure.bypass.parity_audit` to surface counts and blocking
codes. This is the bridge for campaign dashboards and future observability.

## Protected Playground

`PARITYLAB_PLAYGROUND=1` enables the owned career-site playground. It serves a
deterministic jobs catalog and API, hidden trap paths, proof-of-work,
interactive puzzle, HMAC clearance cookie, edge gate decisions, and
`/api/playground/report/<sid>`. The playground classifies local scrape intent
such as recon, pagination walk, detail harvest, API harvest, catalog harvest,
or trap seeking. Full route and artifact details live in
`tools/bot_parity_lab/docs/PLAYGROUND.md`.

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

For the standalone `paritylab` package tests, run from `tools/bot_parity_lab`:

```bash
python -m pytest tests -q
```
