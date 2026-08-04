# Bot Parity Lab

Local-only playground for collecting scraper/browser network behavior and
browser-realm signals.

Run:

```bash
uv run python -m tools.bot_parity_lab.runner --out artifacts/bot_parity_lab
```

The lab starts a `127.0.0.1` static site, records every request, waits for the
page JavaScript probe, and writes:

- `bot_parity_raw.json`
- `bot_parity_report.md`

The detector is intentionally local and deterministic. It checks parity signals
across four defensive layers:

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

The lab intentionally uses localhost evidence. IP reputation, ASN reputation,
TLS/JA3/JA4, HTTP/2, HTTP/3, and vendor-private encrypted challenge payloads are
not claimed as equivalent to production bot-management providers; the local gate
records the closest reproducible surfaces and flags coverage gaps explicitly.

`httpx_raw` and `patchright_plain` are negative controls and are allowed to fail
by default. Any `job_ftch` tier failure returns exit code `2`.

The lab is scoped to `127.0.0.1` and is meant to prove self-consistency of this
project's own browser tiers. It does not target third-party sites.
