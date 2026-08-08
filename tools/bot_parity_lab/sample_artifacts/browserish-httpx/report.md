# Bot / Browser Parity Report

- **Session:** `browserish-httpx-d2a201f9f1eb43169ce1`
- **Client:** `browserish-httpx` (`httpx-header-mimic`)
- **Created:** `2026-08-04T16:41:04.408+00:00`
- **Disposition:** **expected_fail**
- **Suspicion score:** **82/100**
- **Counts:** hard=1, medium=2, low=3, info=3
- **Gate reason:** Negative control produced medium/high findings as intended.

> This is a local defensive audit. The score is heuristic and should be calibrated against your own manual-browser baselines.

## Findings

| Class | Score | Code | Finding | Exact reason |
|---|---:|---|---|---|
| hard_bot_signal | 40 | `JS_WINDOW_PROBE_MISSING` | Window JavaScript probe missing | The page did not execute or submit the primary browser runtime probe. |
| medium_suspicious | 15 | `BEHAVIOR_NO_EVENTS` | No interaction events | No mouse, pointer, keyboard, scroll, focus, or visibility events were observed. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_STATIC_CLASSIC-WORKER.JS` | Expected resource was not requested: /static/classic-worker.js | A normal page load executes a fixed local resource graph; the request is absent. |
| low_entropy_mismatch | 4 | `NET_CACHE_REVALIDATION_ABSENT` | No cache revalidation observed | Repeated local cache probes did not produce a conditional request or 304 response. |
| low_entropy_mismatch | 4 | `NET_FETCH_DEST_MISMATCH` | Fetch request has atypical destination metadata | A JavaScript fetch normally uses Sec-Fetch-Dest: empty. |
| low_entropy_mismatch | 4 | `NET_RESOURCE_MISSING_STATIC_MODULE-WORKER.JS` | Expected resource was not requested: /static/module-worker.js | A normal page load executes a fixed local resource graph; the request is absent. |
| informational | 0 | `HTTP_VERSION_DISTRIBUTION` | HTTP protocol distribution captured | The report includes the server-observed HTTP version for every request. |
| informational | 0 | `IP_REPUTATION_OFFLINE` | Offline IP policy match | No public reputation service is queried. The result comes from the supplied CIDR policy and, when configured, a local MaxMind ASN database. |
| informational | 0 | `TLS_FINGERPRINT_CAPTURED` | TLS fingerprint captured | The passive local proxy parsed the cleartext ClientHello without terminating or modifying TLS. |

## Evidence

### `NET_RESOURCE_MISSING_STATIC_CLASSIC-WORKER.JS`
- Evidence: `{"missing_path": "/static/classic-worker.js"}`

### `NET_CACHE_REVALIDATION_ABSENT`
- Request IDs: `46f6cbc2bbba4ae4b6418bea5ae8862f, 8884fdcabbae44658c84d0d8ab011648`

### `NET_FETCH_DEST_MISMATCH`
- Request IDs: `7d95a56effa243e3919552757012ec1b`
- Evidence: `{"sec_fetch_dest": "document"}`

### `NET_RESOURCE_MISSING_STATIC_MODULE-WORKER.JS`
- Evidence: `{"missing_path": "/static/module-worker.js"}`

### `HTTP_VERSION_DISTRIBUTION`
- Evidence: `{"versions": {"2": 14}}`

### `IP_REPUTATION_OFFLINE`
- Evidence: `{"asn": null, "cidr": "127.0.0.0/8", "country": null, "ip": "127.0.0.1", "label": "loopback", "network_type": "loopback", "organization": null, "risk": 0, "source": "local-policy", "tags": ["local"]}`

### `TLS_FINGERPRINT_CAPTURED`
- Evidence: `{"alpn": ["http/1.1", "h2"], "cipher_count": 30, "extension_count": 13, "ja3": "8ecf4858a704c8936042f20a8bedee04", "ja4": "t13d3013h1_1d37bd780c83_ecd0401ec68b"}`

## TLS / Transport

| Connection | Client | JA3 | JA4 | ALPN | TLS versions | Parse error |
|---|---|---|---|---|---|---|
| `392b3b59d323` | `127.0.0.1:37900` | `8ecf4858a704c8936042f20a8bedee04` | `t13d3013h1_1d37bd780c83_ecd0401ec68b` | http/1.1,h2 | 0x304,0x303 |  |

## Request waterfall

| # | +ms | Duration | HTTP | Connection | Method | Path | Status | Fetch metadata |
|---:|---:|---:|---|---|---|---|---:|---|
| 1 | 0.00 | 1.02 ms | 2 | `392b3b59d323` | GET | `/` | 200 | none/navigate/document |
| 2 | 3.16 | 7.98 ms | 2 | `392b3b59d323` | GET | `/static/style.css` | 200 | none/navigate/document |
| 3 | 12.54 | 1.39 ms | 2 | `392b3b59d323` | GET | `/static/probe.js` | 200 | none/navigate/document |
| 4 | 15.66 | 0.96 ms | 2 | `392b3b59d323` | GET | `/static/pixel.svg` | 200 | none/navigate/document |
| 5 | 17.82 | 0.95 ms | 2 | `392b3b59d323` | GET | `/favicon.ico` | 200 | none/navigate/document |
| 6 | 19.97 | 0.73 ms | 2 | `392b3b59d323` | GET | `/api/cookie/set` | 200 | none/navigate/document |
| 7 | 22.30 | 0.40 ms | 2 | `392b3b59d323` | GET | `/api/cookie/echo` | 200 | none/navigate/document |
| 8 | 24.21 | 0.53 ms | 2 | `392b3b59d323` | GET | `/api/fetch` | 200 | none/navigate/document |
| 9 | 26.18 | 0.41 ms | 2 | `392b3b59d323` | GET | `/api/cacheable` | 200 | none/navigate/document |
| 10 | 28.06 | 0.36 ms | 2 | `392b3b59d323` | GET | `/api/cacheable` | 200 | none/navigate/document |
| 11 | 30.12 | 0.42 ms | 2 | `392b3b59d323` | GET | `/api/redirect/start` | 302 | none/navigate/document |
| 12 | 32.07 | 0.40 ms | 2 | `392b3b59d323` | GET | `/api/redirect/mid` | 307 | none/navigate/document |
| 13 | 33.88 | 0.39 ms | 2 | `392b3b59d323` | GET | `/api/redirect/final` | 200 | none/navigate/document |
| 14 | 35.90 | 0.46 ms | 2 | `392b3b59d323` | POST | `/api/beacon` | 204 | none/navigate/document |

## Header order

1. `host: localhost:8443`
2. `sec-ch-ua: "Chromium";v="151", "Not.A/Brand";v="24"`
3. `sec-ch-ua-mobile: ?0`
4. `sec-ch-ua-platform: "Windows"`
5. `upgrade-insecure-requests: 1`
6. `user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36`
7. `accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8`
8. `sec-fetch-site: none`
9. `sec-fetch-mode: navigate`
10. `sec-fetch-user: ?1`
11. `sec-fetch-dest: document`
12. `accept-encoding: gzip, deflate, br, zstd`
13. `accept-language: en-US,en;q=0.9`

## JavaScript realms

No JavaScript probes.

## Behavioral summary

- Event count: **0**
- Event types: `{}`
- Trusted events: **0 / 0**

## Opaque local payload observations

No opaque payload samples were submitted.

The lab records shape features only. It does not decode or emulate closed vendor payloads.

## Calibration notes

- Build baselines from several headed manual sessions on each supported OS/browser version.
- Treat single low-entropy mismatches as weak evidence; combine independent layers.
- Re-run after browser upgrades because TLS, Client Hints, permissions, and API surfaces change.
- Keep the gate focused on regressions relative to your own accepted baseline, not universal allow/deny claims.
