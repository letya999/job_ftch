# Protected playground

The protected playground is an opt-in, owned localhost career site for testing
the lab's decision layer. It is not enabled by default and never contacts a
third-party target, CAPTCHA provider, reputation API, or token service.

Enable it with:

```bash
PARITYLAB_PLAYGROUND=1 uv run python -m paritylab --out artifacts/playground
```

When enabled, the app adds deterministic career-site routes:

- `/jobs` and `/jobs?page=N`
- `/jobs/<job_id>`
- `/api/jobs?page=N`
- `/api/jobs/<job_id>`
- `/robots.txt` and `/sitemap.xml`
- hidden trap routes under `/trap/*` and `/internal/*`
- `/api/playground/report/<sid>`

Protected catalog and API routes require a local clearance cookie. Missing
clearance triggers a proof-of-work page first. Repeated proof-of-work failure,
rate bursts, or configured fingerprint policy can escalate to an interactive
puzzle, deny, or tarpit decision.

## Intent report

Every protected-site request is classified against the owned catalog. The
report records:

- `intent`: `probe_only`, `recon_only`, `single_page_fetch`,
  `pagination_walk`, `detail_harvest`, `api_harvest`, `catalog_harvest`, or
  `trap_seeker`
- confidence
- trap hits
- distinct job IDs touched
- listing pages and API request count
- catalog coverage ratio
- request velocity and median content gap
- surface counts and sample paths

The classifier answers what the client tried to parse from the local career
site. It does not infer intent for real external sites.

## Challenges and clearance

The challenge engine is local and deterministic enough for tests, but token
values remain opaque:

- proof-of-work: SHA-256 prefix challenge with bounded attempts and TTL
- puzzle: select all circles in a local SVG grid, with duration and pointer
  sample plausibility checks
- clearance: HMAC-signed cookie scoped to the session hash and expiry

Artifacts record only SHA-256 hash prefixes for challenge IDs and clearance
tokens. Raw token or cookie values are not written to reports.

## Gate decisions

The edge gate records every verdict:

- `allow`
- `js_challenge`
- `interactive_challenge`
- `deny`
- `tarpit`

Reasons include clearance state, JA3/JA4 policy lists, rate windows, proof-of-
work failure escalation, and exempt paths. The gate is a reproducible local
model of a bot-management decision layer, not a claim of vendor parity.

## Reports

Final session reports include a `Playground (protected site, gate, challenges)`
section when protected-site evidence exists. The JSON endpoint at
`/api/playground/report/<sid>` returns the current intent, gate decision counts,
recent decisions, drained challenge ledger, fingerprint realms, and trap hits.

Run focused tests from the lab package directory:

```bash
python -m pytest tests -q
```
# Risk-bound clearance

Clearance is session-bound but is not an allow-list bypass. Before every protected request,
the gate evaluates positive evidence already collected from runtime, vendor, rendering and
transport probes. A hard observation denies access; two independent medium observations
escalate to the interactive challenge. Missing or not-yet-arrived probes do not count as
live risk, which avoids penalizing an in-progress capture.

Current live hard evidence includes explicit webdriver exposure, HeadlessChrome identity,
SwiftShader rendering, BotD automation verdicts and CDP marker globals. Current live medium
evidence includes zero outer-window geometry and TLS persona drift. The final report retains
the exact codes that overrode clearance or caused escalation.
