---
title: "CAPTCHA provider rollout"
description: "Operational rollout for observed CAPTCHA/bot-protection handling: project wiring, browser setup, provider roles, and eval gates."
updated: 2026-09-17
---
# CAPTCHA provider rollout

This rollout is based on the 2026-08-02 observe run over the 300 career-site
fixtures plus later Cian/Yandex SmartCaptcha detections. Confirmed interactive
types: `recaptcha`, `smartcaptcha` (Yandex, including Cian `/cian-captcha/` and
`/tmgrdfrend/showcaptcha`). `cloudflare_challenge` is a browser/session
challenge. Other types stay observe-only until a fresh run confirms them.

`smartcaptcha` is a first-class `CaptchaChallengeType`. A paid token is injected
into `smart-token`, the site callback is fired, and the captcha form is submitted.
Clearance is the page leaving `/cian-captcha` / `showcaptcha` / `tmgrdfrend`.
The SmartCaptcha route is `browser_wait` (checkbox, then vision clicks on the
image puzzle) then CapSolver then CapMonster then 2Captcha then `observe`.
Live CapSolver createTask returns `ERROR_TYPE_NOT_SUPPORTED` for
`YandexCaptchaTask` / `YandexSmartCaptchaTask`; that rejection does not
consume the paid slot. 2Captcha token (`YandexSmartCaptchaTaskProxyless`) and
image (`SmartCaptchaTask` coordinates) need `TWOCAPTCHA_API_KEY`.
Encounters stay in OpenObserve as `captcha_encounter` / `captcha_solve_outcome`.

## Project wiring

Provider roles:

| Role | Providers |
|---|---|
| Production candidates | `capsolver`, `capmonster` |
| Benchmark candidate | `nextcaptcha` for `recaptcha` and `turnstile` |
| Free/dev contour | `browser_wait`, `nopecha`, `cliproxy_image`, manual/mock/sandbox fixtures |
| Observe-only until confirmed | `turnstile`, `hcaptcha`, `datadome`, `perimeterx`, `unknown` |
| Yandex SmartCaptcha | `browser_wait` (checkbox + vision clicks), then `capsolver`, `capmonster`, `2captcha` if keyed, else `observe` |

Environment variables:

| Provider | Variable |
|---|---|
| CapSolver | `CAPSOLVER_API_KEY` |
| CapMonster Cloud | `CAPMONSTER_API_KEY` |
| NextCaptcha | `NEXTCAPTCHA_API_KEY` |
| 2Captcha | `TWOCAPTCHA_API_KEY` |
| Anti-Captcha | `ANTICAPTCHA_API_KEY` |
| NopeCHA | `NOPECHA_API_KEY` |
| CLIProxy image OCR | `JOB_FTCH_OPENAI_API_KEY` (or `OPENAI_API_KEY`) plus `JOB_FTCH_CAPTCHA_VISION_BASE_URL` / `JOB_FTCH_CAPTCHA_VISION_MODEL` |

`cliproxy_image` is image-OCR only (after CapSolver). It does not solve recaptcha/turnstile/hcaptcha and does not switch `JOB_FTCH_LLM_GATEWAY`.

Default runtime remains conservative:

```yaml
captcha_provider: nopecha
captcha_enabled_providers:
  - browser_wait
  - nopecha
captcha_provider_routes: {}
```

Paid providers only run when explicitly selected as `captcha_provider` and
included in `captcha_enabled_providers`.

## Domain authorization (`captcha_authorized_domains`)

Paid/external solving (CapSolver, CapMonster, NextCaptcha, `cliproxy_image`,
…) is gated by the domain allowlist. `browser_wait` is never gated.

Env: `JOB_FTCH_CAPTCHA_AUTHORIZED_DOMAINS`. The `JOB_FTCH_` prefix is required
— bare `CAPTCHA_AUTHORIZED_DOMAINS` is ignored. Env overrides runtime YAML;
an empty env value therefore **denies all domains** even if YAML has `*`.

| Allowlist | Behavior |
|---|---|
| empty | deny for every domain (code default and `.env.prod.example`) |
| `hh.ru,m.hh.ru` | suffix match covers subdomains |
| `*` | wildcard: authorize every domain |

**Local docker-dev uses the wildcard.** Set both:

```text
JOB_FTCH_CAPTCHA_AUTHORIZED_DOMAINS=*
```

in `.env.dev` (see `.env.dev.example`) and `captcha_authorized_domains: ["*"]`
in `config/runtime.dev.yaml`. Without `*`, image fallback CapSolver →
CLIProxy OCR never runs. Production stays empty unless you opt in.

Since 2026-09-13 the wildcard is supported in both config paths
(`CaptchaSolverBypass._domain_authorized` and
`_authorized_domains_from_config`).

Solver guardrails:

```yaml
captcha_solver_timeout_budget_seconds: 40
captcha_solver_backoff_seconds: 300
```

The provider path waits briefly for a challenge marker/sitekey before creating a
task. A CapSolver image token that injects but does not clear the page is not a
solved challenge: the image chain continues to `cliproxy_image` in the same
`solve()` call. In-process backoff starts only after the whole chain is
exhausted, so HH `/account/captcha` does not skip CLIProxy OCR after the first
CapSolver miss. Paid budget exhaustion on CapSolver also does not skip the free
OCR fallback.

Suggested eval routes:

| Challenge type | Route |
|---|---|
| `recaptcha` | `capsolver -> capmonster -> nextcaptcha -> nopecha -> manual_required` |
| `turnstile` | `capsolver -> capmonster -> nextcaptcha -> manual_required` after authorized eval |
| `cloudflare_challenge` | `browser_wait -> capsolver -> manual_required` only for authorized eval domains with a static/sticky proxy |
| `hcaptcha` | `observe` until the fixture run confirms real frequency |
| `image` | `capsolver -> cliproxy_image -> observe` |
| `datadome`, `perimeterx`, `unknown` | `observe -> manual_required`; no provider solve by default |

Example benchmark route:

```yaml
captcha_enabled_providers:
  - browser_wait
  - nopecha
  - capsolver
  - capmonster
  - nextcaptcha
  - cliproxy_image
captcha_provider_routes:
  image:
    - capsolver
    - cliproxy_image
    - observe
  recaptcha:
    - capsolver
    - capmonster
    - nextcaptcha
    - nopecha
    - manual_required
  turnstile:
    - capsolver
    - capmonster
    - nextcaptcha
    - manual_required
  cloudflare_challenge:
    - browser_wait
    - capsolver
    - manual_required
```

## Browser setup

Use a separate browser profile for ingest diagnostics. Do not reuse a personal
daily browser profile.

Runtime artifacts belong under ignored `.runtime/` paths:

```text
.runtime/browser_profiles/job_ftch_ingest_profile/
.runtime/session_states/
.runtime/runs/
```

Enable a controlled warmed profile only for an ingest-specific directory:

```text
JOB_FTCH_BROWSER_PROFILE_DIR=.runtime/browser_profiles/job_ftch_ingest_profile/
JOB_FTCH_BROWSER_PROFILE_PERSISTENT=true
```

Browser profile checklist:

- JavaScript enabled.
- Cookies enabled.
- No unrelated extensions during baseline eval.
- Normal viewport/device profile.
- Headed browser available for manual challenge checks.
- Saved session state never committed.

For `cloudflare_challenge`, first validate whether a manual browser pass creates
stable cookies/session state. Treat it as a browser/session problem before using
paid CAPTCHA APIs.

## Cloudflare Challenge with CapSolver

`cloudflare_challenge` is handled as a session challenge, not as a token-only
CAPTCHA. The runtime therefore verifies actual clearance cookies after the
provider returns; challenge HTML or visible Cloudflare verification text is
still a failed route even if a provider call completed.

Operational requirements:

- Run only on owned or explicitly authorized eval targets.
- Authorize the target domain through `captcha_authorized_domains`; the
  protected matrix runner populates this from the selected target domains.
- Use `capsolver` as the selected paid provider and keep `browser_wait` first
  in the route, so easy browser/session clears do not spend provider balance.
- Use a static or sticky residential proxy endpoint shared by the browser route
  and the provider task. Prefer `JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST` for
  this path; it is prepended to the residential pool before
  `config/proxies.yaml` and `JOB_FTCH_RESIDENTIAL_PROXY_LIST`.
- Keep the browser user-agent stable for the route. The CapSolver
  `AntiCloudflareTask` payload uses `websiteURL`, the compact proxy string and
  the live browser user-agent.

CapSolver rejects dynamic proxy hostnames for `AntiCloudflareTask`. The
provider adapter resolves proxy hostnames to an IP before task creation to avoid
that class of rejection, but the resolved endpoint still has to be reachable and
sticky enough for Cloudflare clearance to bind to the same browser route.

Protected matrix preflight blocks paid CapSolver runs when only gateway-mode
residential proxy config is available. Use one of these explicit raw/static
inputs before running a Cloudflare eval:

```text
JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST=http://static-resi.example:9000
```

or:

```yaml
residential:
  - http://static-resi.example:9000
```

The dedicated env is safest for experiments because it does not change the
general proxy pool ordering outside the current process.

Known external blocker signatures:

| Provider error | Meaning |
|---|---|
| `Your proxy host uses dynamic DNS` | The configured proxy gateway is not acceptable for CapSolver Cloudflare tasks; use a region-specific static/sticky endpoint. |
| `proxy timeout or other issues` | CapSolver could not reach or use the proxy within its task budget; validate the proxy from the same region and credentials before rerunning the matrix. |
| `invalid html` | Do not send page HTML for this task shape; the current adapter intentionally omits it. |

## Eval gate

Run provider comparison only on owned or explicitly authorized test targets.
Measure:

| Metric | Meaning |
|---|---|
| verified success | Challenge cleared and ingest continues |
| p50/p95 latency | Provider and browser wait cost |
| timeout rate | Deadline compatibility |
| cost per success | Paid provider efficiency |
| ingest uplift | Additional `parsed_ok` sources versus observe baseline |

Promotion rule: a provider becomes default for a challenge type only after it
beats the current route on verified success and cost under the same deadline
budget.

Provider-only smoke benchmark:

```powershell
uv run python scripts/eval/run_captcha_provider_eval.py `
  --url "https://example.test/page-with-recaptcha" `
  --sitekey "SITEKEY_FROM_AUTHORIZED_TEST_PAGE" `
  --challenge-type recaptcha `
  --providers capsolver,capmonster,nextcaptcha,nopecha `
  --out-json .runtime/runs/captcha_provider_eval_recaptcha.json
```

Add `--allow-paid` only when the selected providers are funded and the target
page is owned or explicitly authorized for testing.

## Rehearsal log

### 2026-09-13 — hh.ru cannot be provoked onto CAPTCHA, provider token path re-verified

Provider smoke (`scripts/eval/run_captcha_provider_eval.py --allow-paid`,
Google reCAPTCHA v2 demo, keys funded):

- `capsolver` solved `recaptcha` (token kind) in ~18 s, token present;
- session flow still requires HH-style sites to actually serve a CAPTCHA.

Session stress on hh.ru (residential DataImpulse, patchright headless,
`scripts/hh_ai_dev_probe.py` / churn + parallel sessions):

- ~30 sessions and ~200 navigations: search pages and vacancy cards never
  served a CAPTCHA (`captchaText` absent, no reCAPTCHA iframe);
- HH escalates via intermediate `blocked_fingerprint` HTTP 200 pages during
  session open and single 403s on detail URLs, cleared transparently by the
  same-session `browser.challenge_retry`;
- verdict: hh.ru CAPTCHA solving needs a live CAPTCHA trigger that is still not
  reproducible in this environment; keep CapSolver wired, do not interpret
  absence of CAPTCHA as solver untested.

Same-day site rehearsal (`scripts/captcha_rehearsal.py`,
`.runtime/runs/captcha_rehearsal/`):

- kadrof.ru and telecom.kz loaded clean from residential geo-RU — no recaptcha
  shown at 10 rapid same-site navigations each, unlike the 2026-08 observed run;
- airastana.com: captcha-class `incapsula` reproduce output — hard session
  gate, `browser_wait` does not clear it, provider chain correctly stops at
  `observe` (fail-closed). Sites behind Incapsula stay manual/HITL until an
  approved provider integration.
- ozon.tech/vacancies/: hard `fab_chlg_` Antibot Challenge Page (403, JS-only
  shell from `st.ozone.ru/s3/abt-challenge/script_v47_1.js`). Chromium
  patchright tier cannot pass it (`browser_wait` + reload cycles keep the
  403 challenge); the `camoufox` tier cleared it on the first wait+reload
  cycle with no paid provider call: challenge gone, 20 vacancy cards listed,
  2 vacancy detail pages extracted in the same session. Ozon antibot must be
  routed through the camoufox tier, not through provider CAPTCHA solving.

Takeaway: `*` domain authorization + funded keys make provider solving live for
all authorized targets; per-site CAPTCHA appearance remains rate/fingerprint
dependent and must be re-evaluated against production telemetry rather than
assumed from `observe` runs.
