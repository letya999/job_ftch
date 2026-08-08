---
title: "CAPTCHA provider rollout"
description: "Operational rollout for observed CAPTCHA/bot-protection handling: project wiring, browser setup, provider roles, and eval gates."
updated: 2026-08-05
---
# CAPTCHA provider rollout

This rollout is based on the 2026-08-02 observe run over the 300 career-site
fixtures. The only confirmed CAPTCHA subtype in that run was `recaptcha`;
`cloudflare_challenge` was observed separately as a browser/session challenge.
Other types stay observe-only until a fresh run confirms them.

## Project wiring

Provider roles:

| Role | Providers |
|---|---|
| Production candidates | `capsolver`, `capmonster` |
| Benchmark candidate | `nextcaptcha` for `recaptcha` and `turnstile` |
| Free/dev contour | `browser_wait`, `nopecha`, manual/mock/sandbox fixtures |
| Observe-only until confirmed | `turnstile`, `hcaptcha`, `datadome`, `perimeterx`, `image`, `unknown` |

Environment variables:

| Provider | Variable |
|---|---|
| CapSolver | `CAPSOLVER_API_KEY` |
| CapMonster Cloud | `CAPMONSTER_API_KEY` |
| NextCaptcha | `NEXTCAPTCHA_API_KEY` |
| 2Captcha | `TWOCAPTCHA_API_KEY` |
| Anti-Captcha | `ANTICAPTCHA_API_KEY` |
| NopeCHA | `NOPECHA_API_KEY` |

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

Solver guardrails:

```yaml
captcha_solver_timeout_budget_seconds: 40
captcha_solver_backoff_seconds: 300
```

The provider path waits briefly for a challenge marker/sitekey before creating a
task. Recent domain+challenge failures are backed off in-process, so one bad
sitekey/action does not burn the whole paid budget.

Suggested eval routes:

| Challenge type | Route |
|---|---|
| `recaptcha` | `capsolver -> capmonster -> nextcaptcha -> nopecha -> manual_required` |
| `turnstile` | `capsolver -> capmonster -> nextcaptcha -> manual_required` after authorized eval |
| `cloudflare_challenge` | `browser_wait -> capsolver -> manual_required` only for authorized eval domains with a static/sticky proxy |
| `hcaptcha` | `observe` until the fixture run confirms real frequency |
| `datadome`, `perimeterx`, `unknown` | `observe -> manual_required`; no provider solve by default |

Example benchmark route:

```yaml
captcha_enabled_providers:
  - browser_wait
  - nopecha
  - capsolver
  - capmonster
  - nextcaptcha
captcha_provider_routes:
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
