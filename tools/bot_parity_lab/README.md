# Bot Parity Lab

Local-only playground for collecting scraper/browser network behavior,
TLS/transport evidence, browser-realm signals, and owned-client parity reports.


Run:

```bash
uv run python -m tools.bot_parity_lab.runner --out artifacts/bot_parity_lab
```

Full HTTPS/TLS parity campaign against the real `job_ftch` browser tiers:

```bash
uv run python scripts/eval/run_bot_browser_parity_lab.py \
  --tiers patchright_browser,nodriver,camoufox,cloak \
  --out artifacts/bot_parity_lab
```

The lightweight runner starts a `127.0.0.1` static site, records every request,
waits for the page JavaScript probe, and writes:

- `bot_parity_raw.json`
- `bot_parity_report.md`

The detector is intentionally local and deterministic. It checks parity signals
across four defensive layers:

- TLS/transport: passive ClientHello capture, JA3/JA4 evidence, TLS versions,
  cipher suites, extension IDs, ALPN, SNI, HTTP/1.1, HTTP/2, and optional HTTP/3.
- Network: request waterfall, missing static resources, redirects, favicon,
  pixels, `Accept*`, `Sec-Fetch-*`, `Sec-CH-UA*`, header order, and connection
  reuse.
- Runtime: `navigator.webdriver`, UA Client Hints, plugins, mime types,
  `window.chrome`, storage, media devices, permissions, WebGL, canvas, audio,
  fonts, and native-function shape.
- Cross-realm: window, iframe, classic worker, and module worker consistency
  for UA, language, platform, timezone, hardware/memory, and WebGL. The
  vendor-style mode also probes ServiceWorker and SharedWorker realms.
- Behavior: pointer/mouse trail, scroll, click, keyboard, focus, and
  `navigator.userActivation`.
- Session and tripwires: local identity history, temporal drift across probes,
  hidden honeypot field, local CAPTCHA-style challenge request, IP/socket class,
  HTTP protocol version, touch/media/sensor shape, and movement straightness.

The deep catalog expands those layers into 250+ atomic checks per browser run
and reports the evaluated `signals` count in `bot_parity_report.md`. It is
expected to detect the project's own bypass tiers while this lab is being used
as a red-team gate; pass/fail allowlists are controlled by `--allow-fail-clients`.

The lab intentionally uses localhost evidence. External IP reputation, ASN
reputation, CDN-side policy, and vendor-private encrypted challenge payloads are
not claimed as equivalent to production bot-management providers; the local gate
records the closest reproducible surfaces and flags coverage gaps explicitly.

`httpx_raw` and `patchright_plain` are negative controls and are allowed to fail
by default. Any `job_ftch` tier failure returns exit code `2`.

The lab is scoped to `127.0.0.1` and is meant to prove self-consistency of this
project's own browser tiers. It does not target third-party sites.

## Protected playground

Set `PARITYLAB_PLAYGROUND=1` to add the owned protected career-site routes,
local proof-of-work, interactive puzzle, HMAC clearance cookie, edge gate
decisions, trap paths, and scrape-intent report. See
[`docs/PLAYGROUND.md`](docs/PLAYGROUND.md) for the route and artifact contract.

## Knowledge system

The lab includes a versioned, machine-readable encyclopedia of browser/network surfaces, detection mechanics and countermeasures. Coverage distinguishes implemented, partial, planned and knowledge-only work.

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/COVERAGE.md`](docs/COVERAGE.md)
- [`docs/MECHANICS.md`](docs/MECHANICS.md)
- [`docs/OSS_COMPONENTS.md`](docs/OSS_COMPONENTS.md)
- [`docs/SIGNALS.md`](docs/SIGNALS.md)

Validate generated knowledge pages with `python scripts/catalog_docs.py --check --json`.

## Owned protection fixtures

`/fixtures/protection/<fixture>` serves inert local protection pages for
detector regression only: `waf_block`, `captcha_recaptcha`,
`passive_challenge`, and `qrator_jsid`. They contain no provider script, token
endpoint, clearance cookie, or solve route. The test suite asserts that each is
classified before parsing through the shared challenge classifier.

Challenge-bearing fixtures also expose a local lifecycle contract at
`/api/protection/<fixture>/contract`. It models type, synthetic sitekey,
action, minScore, deadline reserve, and a `manual_required` response decision.
It is an observability/control-plane fixture: `solve_supported` is always
false, provider task creation is always false, and no token or cookie value is
ever produced.
