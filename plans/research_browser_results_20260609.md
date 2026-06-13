# Research Report: Browser/JS Support for Career Site Sources

## Section 1: browser.py key capabilities (jobseek)

| Feature | jobseek implementation | Needed for "most sites" |
| :--- | :--- | :--- |
| **Stealth Mode** | `launch_persistent_context` + `channel="chrome"` + `--disable-blink-features=AutomationControlled`. Uses `--headless=new`. | **Yes** (Essential for Cloudflare basic challenges and bot detection). |
| **Action Pipeline** | `run_actions` supporting `click`, `wait`, `evaluate`, `dismiss_overlays`, `repeat`, `paginate_collect`. | **Yes** (Critical for pagination and "Load More" buttons). |
| **Wait Strategies** | Supports `networkidle`, `domcontentloaded`, `load`, `commit`. Includes a fallback strategy (e.g., if `networkidle` times out, try `domcontentloaded`). | **Yes** (Handles SPAs that never finish loading analytics/telemetry). |
| **Cookie Banner Dismissal** | `dismiss_overlays` uses a list of CSS selectors (`OVERLAY_SELECTORS`) to remove banners via `page.evaluate`. | **Yes** (Banners often block clicks on "Next" or "Job" links). |
| **Safe Content Fetch** | `safe_content()` retries `page.content()` if a navigation race error occurs. | **Yes** (Stability for SPA sites). |
| **Proxy Support** | Integrated `playwright_proxy_for` with rotating providers (Webshare, Decodo). | **Optional** (Can be added later). |
| **Xvfb / Headless** | `_resolve_headless` detects if Xvfb is running to allow `headless: false` in Docker. | **Optional** (Most sites work with `--headless=new`). |
| **Cookie Injection** | `context.add_cookies` with `{uuid}` placeholder replacement. | **Optional** (Rarely used). |

### BROWSER_KEYS complete list
- `wait`: Primary wait strategy (default: `networkidle`).
- `wait_fallback`: Fallback strategy if primary times out (default: `domcontentloaded`).
- `timeout`: Navigation timeout (default: 30s).
- `user_agent`: Custom UA string.
- `headless`: Whether to run in headless mode (default: `true`).
- `stealth`: Enables `--headless=new` and other masks.
- `actions`: List of actions to execute after navigation.
- `warmup_url`: URL to visit before the target URL (to set initial cookies/session).
- `cookies`: List of cookies to inject.
- `disable_http2`: Force HTTP/1.1 (sometimes avoids H2 fingerprinting).
- `persistent_context`: Use `launch_persistent_context` for real-Chrome profile.
- `channel`: Browser channel (e.g., `"chrome"`, `"msedge"`).
- `viewport`: Viewport size (default: 1440x900).
- `locale`: Browser locale (default: `en-US`).
- `skip_ssl`: Ignore HTTPS errors.

---

## Section 2: Coverage gap table

| Site category | Current job_ftch support | Missing piece |
| :--- | :--- | :--- |
| **Standard ATS** (Greenhouse, Lever, etc.) | **Full** (Uses official/internal APIs via httpx). | N/A |
| **Sitemap-based sites** | **Full** (Static XML parsing). | N/A |
| **SPA sites** (React/Vue/Next.js) | **None** (DOM monitor only fetches static HTML). | Playwright rendering (`render: true`). |
| **Cookie banner blocking** | **None** (Cannot dismiss overlays). | `dismiss_overlays` action in pipeline. |
| **Basic Cloudflare** (JS challenge) | **Limited** (Uses static httpx with fixed UA). | Stealth browser context (`--headless=new`). |
| **Pagination requiring JS click** | **None** (DOM monitor doesn't support actions). | `paginate_collect` and `repeat` actions. |
| **IP-blocked sites** | **None** (No proxy support). | Proxy provider layer. |

---

## Section 3: Minimal browser.py design for job_ftch

### What to port
- **`open_page` / `_open_persistent_page`**: Simplified version focusing on `persistent_context` and `stealth`.
- **`navigate`**: With `wait_fallback` support.
- **`run_actions`**: Implementing `click`, `wait`, `evaluate`, `dismiss_overlays`, `repeat`, `paginate_collect`.
- **`safe_content`**: Essential for preventing crashes on SPAs.
- **`render`**: Convenience wrapper for easy integration into monitors/scrapers.

### What to skip
- **Xvfb/xdpyinfo**: job_ftch should prioritize `headless: true` with `--headless=new`.
- **Metrics**: Prometheus/Inc. metrics can be added later if needed for a daemon.
- **Complex ProxyProviders**: Start with a simple env-based proxy if needed.
- **`_resolve_placeholders`**: UUID injection in cookies is a niche feature.

**Estimated code lines:** ~350 lines.

---

## Section 4: Extra ATS monitors portability table

| Monitor name | Uses browser? | API available? | Port effort |
| :--- | :--- | :--- | :--- |
| **accenture** | No | Yes (Elastic/Search API) | Low |
| **almacareer** | No | Yes | Low |
| **amazon** | No | Yes (search.json) | Low |
| **bite** | No | Yes | Low |
| **deel** | No | Yes (guest/ats API) | Low |
| **dvinci** | No | Yes | Low |
| **eightfold** | No | Yes (Sitemap + PCSX) | Low |
| **gem** | No | Yes | Low |
| **hireology** | No | Yes | Low |
| **inline** | **Yes** | No | Medium |
| **jobylon** | No | Yes | Low |
| **join** | No | Yes | Low |
| **mokahr** | No | Yes | Low |
| **notion** | No | Yes | Low |
| **oracle_hcm** | No | Yes (REST API) | Low |
| **phenom** | No | Yes | Low |
| **pinpoint** | No | Yes | Low |
| **recruiter_co_kr** | No | Yes | Low |
| **softgarden** | No | Yes | Low |
| **traffit** | No | Yes | Low |
| **umantis** | No | Yes | Low |

---

## Section 5: Recommended implementation phases

### Phase 1: Basic Playwright Support
1.  **Create `job_ftch/infrastructure/sources/browser.py`**: Minimal implementation of `jobseek`'s browser engine.
2.  **Update `job_ftch/infrastructure/sources/monitors/dom.py`**:
    - Add `render: true` support.
    - Integrate `actions` pipeline.
    - Support `url_filter` regex.
    - Implement `_paginate_urls` with `browser: true` support (using `fetch` inside page).

### Phase 2: High-Value ATS Porting
1.  **Port Eightfold monitor**: Large coverage for enterprise sites. Uses Sitemap + API.
2.  **Port Amazon and Deel monitors**: Specific high-interest boards.
3.  **Port Oracle HCM monitor**: Broad corporate coverage.

### Phase 3: Advanced Scraper Integration
1.  **Update `jsonld` and `nextdata` scrapers**: Add `render: true` option to handle SPAs where the data tags are injected by JS.
2.  **Implement `api_sniffer` monitor**: (Optional/Advanced) to handle sites that hide their job lists behind complex XHR requests.
