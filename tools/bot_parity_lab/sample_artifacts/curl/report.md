# Bot / Browser Parity Report

- **Session:** `curl-1076e7b198e64a659284`
- **Client:** `curl` (`curl-libcurl`)
- **Created:** `2026-08-04T16:40:13.472+00:00`
- **Disposition:** **expected_fail**
- **Suspicion score:** **100/100**
- **Counts:** hard=4, medium=9, low=2, info=3
- **Gate reason:** Negative control produced medium/high findings as intended.

> This is a local defensive audit. The score is heuristic and should be calibrated against your own manual-browser baselines.

## Findings

| Class | Score | Code | Finding | Exact reason |
|---|---:|---|---|---|
| hard_bot_signal | 40 | `JS_WINDOW_PROBE_MISSING` | Window JavaScript probe missing | The page did not execute or submit the primary browser runtime probe. |
| hard_bot_signal | 40 | `NET_RESOURCE_MISSING_STATIC_PROBE.JS` | Expected resource was not requested: /static/probe.js | A normal page load executes a fixed local resource graph; the request is absent. |
| hard_bot_signal | 40 | `NET_SEC_FETCH_ABSENT` | Sec-Fetch metadata is absent | The navigation lacks most Fetch Metadata headers expected from current browsers. |
| hard_bot_signal | 40 | `NET_UA_NON_BROWSER` | Non-browser User-Agent | The declared client is a raw HTTP library rather than a browser engine. |
| medium_suspicious | 15 | `BEHAVIOR_NO_EVENTS` | No interaction events | No mouse, pointer, keyboard, scroll, focus, or visibility events were observed. |
| medium_suspicious | 15 | `NET_ACCEPT_ENCODING_MISSING` | Accept-Encoding is missing | A browser usually negotiates compressed content encodings. |
| medium_suspicious | 15 | `NET_ACCEPT_LANGUAGE_MISSING` | Accept-Language is missing | A browser profile normally sends at least one preferred language. |
| medium_suspicious | 15 | `NET_ACCEPT_NAV_MISMATCH` | Navigation Accept header is atypical | Top-level browser navigation usually advertises HTML and related document formats. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_API_BEACON` | Expected resource was not requested: /api/beacon | A normal page load executes a fixed local resource graph; the request is absent. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_API_CACHEABLE` | Expected resource was not requested: /api/cacheable | A normal page load executes a fixed local resource graph; the request is absent. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_STATIC_CLASSIC-WORKER.JS` | Expected resource was not requested: /static/classic-worker.js | A normal page load executes a fixed local resource graph; the request is absent. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_STATIC_PIXEL.SVG` | Expected resource was not requested: /static/pixel.svg | A normal page load executes a fixed local resource graph; the request is absent. |
| medium_suspicious | 15 | `NET_RESOURCE_MISSING_STATIC_STYLE.CSS` | Expected resource was not requested: /static/style.css | A normal page load executes a fixed local resource graph; the request is absent. |
| low_entropy_mismatch | 4 | `NET_RESOURCE_MISSING_FAVICON.ICO` | Expected resource was not requested: /favicon.ico | A normal page load executes a fixed local resource graph; the request is absent. |
| low_entropy_mismatch | 4 | `NET_RESOURCE_MISSING_STATIC_MODULE-WORKER.JS` | Expected resource was not requested: /static/module-worker.js | A normal page load executes a fixed local resource graph; the request is absent. |
| informational | 0 | `HTTP_VERSION_DISTRIBUTION` | HTTP protocol distribution captured | The report includes the server-observed HTTP version for every request. |
| informational | 0 | `IP_REPUTATION_OFFLINE` | Offline IP policy match | No public reputation service is queried. The result comes from the supplied CIDR policy and, when configured, a local MaxMind ASN database. |
| informational | 0 | `TLS_FINGERPRINT_CAPTURED` | TLS fingerprint captured | The passive local proxy parsed the cleartext ClientHello without terminating or modifying TLS. |

## Evidence

### `NET_RESOURCE_MISSING_STATIC_PROBE.JS`
- Evidence: `{"missing_path": "/static/probe.js"}`

### `NET_SEC_FETCH_ABSENT`
- Request IDs: `3d40ad6e211749ddbebdc634c1d060bb`
- Evidence: `{"missing": ["sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user"]}`

### `NET_UA_NON_BROWSER`
- Request IDs: `3d40ad6e211749ddbebdc634c1d060bb`
- Evidence: `{"user_agent": "curl/8.10.1"}`

### `NET_ACCEPT_ENCODING_MISSING`
- Request IDs: `3d40ad6e211749ddbebdc634c1d060bb`

### `NET_ACCEPT_LANGUAGE_MISSING`
- Request IDs: `3d40ad6e211749ddbebdc634c1d060bb`

### `NET_ACCEPT_NAV_MISMATCH`
- Request IDs: `3d40ad6e211749ddbebdc634c1d060bb`
- Evidence: `{"accept": "*/*"}`

### `NET_RESOURCE_MISSING_API_BEACON`
- Evidence: `{"missing_path": "/api/beacon"}`

### `NET_RESOURCE_MISSING_API_CACHEABLE`
- Evidence: `{"missing_path": "/api/cacheable"}`

### `NET_RESOURCE_MISSING_STATIC_CLASSIC-WORKER.JS`
- Evidence: `{"missing_path": "/static/classic-worker.js"}`

### `NET_RESOURCE_MISSING_STATIC_PIXEL.SVG`
- Evidence: `{"missing_path": "/static/pixel.svg"}`

### `NET_RESOURCE_MISSING_STATIC_STYLE.CSS`
- Evidence: `{"missing_path": "/static/style.css"}`

### `NET_RESOURCE_MISSING_FAVICON.ICO`
- Evidence: `{"missing_path": "/favicon.ico"}`

### `NET_RESOURCE_MISSING_STATIC_MODULE-WORKER.JS`
- Evidence: `{"missing_path": "/static/module-worker.js"}`

### `HTTP_VERSION_DISTRIBUTION`
- Evidence: `{"versions": {"2": 7}}`

### `IP_REPUTATION_OFFLINE`
- Evidence: `{"asn": null, "cidr": "127.0.0.0/8", "country": null, "ip": "127.0.0.1", "label": "loopback", "network_type": "loopback", "organization": null, "risk": 0, "source": "local-policy", "tags": ["local"]}`

### `TLS_FINGERPRINT_CAPTURED`
- Evidence: `{"alpn": ["h2", "http/1.1"], "cipher_count": 30, "extension_count": 13, "ja3": "32e4b8812cda0c0d50783b438492a769", "ja4": "t13d3013h2_1d37bd780c83_8537cf56674e"}`

## TLS / Transport

| Connection | Client | JA3 | JA4 | ALPN | TLS versions | Parse error |
|---|---|---|---|---|---|---|
| `508777bc1e72` | `127.0.0.1:52000` | `32e4b8812cda0c0d50783b438492a769` | `t13d3013h2_1d37bd780c83_8537cf56674e` | h2,http/1.1 | 0x304,0x303 |  |
| `02a662f0e49b` | `127.0.0.1:52010` | `32e4b8812cda0c0d50783b438492a769` | `t13d3013h2_1d37bd780c83_8537cf56674e` | h2,http/1.1 | 0x304,0x303 |  |

## Request waterfall

| # | +ms | Duration | HTTP | Connection | Method | Path | Status | Fetch metadata |
|---:|---:|---:|---|---|---|---|---:|---|
| 1 | 0.00 | 1.06 ms | 2 | `508777bc1e72` | GET | `/` | 200 |  |
| 2 | 1.87 | 0.66 ms | 2 | `508777bc1e72` | GET | `/api/cookie/set` | 200 |  |
| 3 | 3.27 | 0.39 ms | 2 | `508777bc1e72` | GET | `/api/cookie/echo` | 200 |  |
| 4 | 4.17 | 0.50 ms | 2 | `508777bc1e72` | GET | `/api/fetch` | 200 |  |
| 5 | 5.19 | 0.45 ms | 2 | `508777bc1e72` | GET | `/api/redirect/start` | 302 |  |
| 6 | 6.00 | 0.34 ms | 2 | `508777bc1e72` | GET | `/api/redirect/mid` | 307 |  |
| 7 | 6.68 | 0.31 ms | 2 | `508777bc1e72` | GET | `/api/redirect/final` | 200 |  |

## Header order

1. `host: localhost:8443`
2. `user-agent: curl/8.10.1`
3. `accept: */*`

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
