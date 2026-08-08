# State-of-the-art audit

Audit date: 2026-08-05.

## Achieved local scope

- 38/38 versioned detection surfaces are implemented; weighted catalog coverage is 1.0.
- 27 bypass mechanics map detection evidence to countermeasures and validation receipts.
- 43 canonical finding codes remain owned, explainable and independent of vendor verdicts.
- Browser evidence covers window/iframe/workers, integrity, rendering, WebGPU readback, fonts,
  media/codecs/devices, storage/origin behavior, timing, WebRTC and enterprise-like interaction.
- Transport evidence covers ClientHello/JA3/JA4, TLS lifecycle, HTTP/2, HTTP/3, QUIC, DNS and
  privacy-filtered TCP/IP metadata.
- FingerprintJS 5.2.0, ThumbmarkJS 1.10.1 and BotD 2.0.0 run as local checksum-pinned ESM
  oracles. Thumbmark logging is disabled. Their results are namespaced evidence, never policy.
- TShark JSON has a privacy-filtering adapter; raw addresses, ports and packets are not persisted.
- Scoring, TLS, behavior, protocol, routes and browser collectors have explicit domain modules.
- A real installed-wheel Patchright run produced deep evidence and all three `vendor:*` records.
- Automated baseline profiles are complete for headed/headless Playwright, Patchright, Nodriver,
  Camoufox, raw HTTPX and curl controls.
- Root repository security scan passed Gitleaks, TruffleHog, Opengrep, Bandit, Ruff and pip-audit.

## Honest boundaries

- This is a local defensive measurement and knowledge lab, not a globally distributed WAF/CDN,
  reputation network, managed challenge platform or production traffic decision service.
- The catalog is exhaustive for schema version 1.0.0, not for every future browser/API/private
  signal. New surfaces enter through the catalog version and must carry a collector/analyzer test.
- Headed Chromium, Firefox and WebKit baseline profiles are automated through isolated Playwright
  adapters; the Chromium control uses the installed stable Chrome channel.
- The curl-impersonate/uTLS negative profile requires an external controlled fixture not installed
  in this environment.
- Creep research corpus remains review-only because public hosting/name use needs trademark-policy
  review; it is not silently vendored or executed.
- Strict mypy currently reports 96 errors, concentrated in the legacy snapshot compatibility
  scorer and pre-existing broad JSON/report typing. Runtime tests, Ruff, JS syntax, installed CLI,
  browser receipts and security gates pass; zero-error strict typing remains engineering debt.
- Enterprise equivalence to Qrator, Akamai or DataDome cannot be claimed without distributed edge
  telemetry, continuously refreshed reputation data, production calibration and an operations team.

## Reproduction gates

```bash
python scripts/catalog_docs.py --check --json
python scripts/audit_oss_registry.py
python -m pytest -q
python -m ruff check .
uv run paritylab run-client patchright --expect pass
python scripts/baseline_audit.py baseline_artifacts
ai-repo-safety scan --target .
```
