---
title: "CAPTCHA provider rollout"
description: "Operational rollout for observed CAPTCHA/bot-protection handling: project wiring, browser setup, provider roles, and eval gates."
updated: 2026-08-02
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
| Benchmark candidate | `nextcaptcha` for `recaptcha` only |
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

Suggested eval routes:

| Challenge type | Route |
|---|---|
| `recaptcha` | `capsolver -> capmonster -> nextcaptcha -> nopecha -> manual_required` |
| `cloudflare_challenge` | `browser_wait/browser_session -> capsolver experimental -> capmonster experimental -> manual_required` |
| `turnstile`, `hcaptcha` | `observe` until the fixture run confirms real frequency |
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
  cloudflare_challenge:
    - browser_wait
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
