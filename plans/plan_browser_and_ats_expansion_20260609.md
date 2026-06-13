# Plan: Browser Infrastructure + ATS Monitor Expansion

## Goal

Cover the majority of career sites that require JS rendering, cookie banner
dismissal, or JS-driven pagination, plus add 4 new ATS monitors (Deel,
Softgarden, Join, Eightfold-simplified) that work via pure httpx APIs.

## Context

- job_ftch already has: 15 monitors, 9 scrapers, CareerSiteSource runtime
- Source to adapt: C:\Users\User\a_projects\jobseek\apps\crawler\src\shared\browser.py
- Source to adapt: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\dom.py
- Source for new monitors: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\

## Phase 1: browser_utils.py — Minimal Playwright Executor

### File to CREATE: job_ftch/infrastructure/sources/browser_utils.py

Port from jobseek's `shared/browser.py` with the following adaptations:

#### What to INCLUDE
1. `BROWSER_KEYS` frozenset — the same set of config keys
2. `DEFAULT_USER_AGENT` — same Chrome UA string
3. `DEFAULT_WAIT = "networkidle"`
4. `DEFAULT_WAIT_FALLBACK = "domcontentloaded"`
5. `DEFAULT_TIMEOUT = 30_000`
6. `CONTEXT_TIMEOUT = 120_000`
7. `OVERLAY_SELECTORS` tuple — same CSS selectors for cookie banners
8. `open_page(pw, config, *, use_proxy=False)` — asynccontextmanager
   - Remove all `metrics.xxx` calls entirely
   - Remove `_resolve_headless()` / Xvfb logic: always use `headless=True` default,
     allow `headless: false` config key without the Xvfb guard (just let Playwright
     fail naturally on Windows desktop if needed — job_ftch is not a server daemon)
   - Keep: stealth, persistent_context, channel, viewport, locale, user_agent,
     cookies (without uuid placeholder), warmup_url, disable_http2, skip_ssl
   - `use_proxy` parameter: read proxy URL from env var `JOB_FTCH_HTTP_PROXY`
     (simple: `os.environ.get("JOB_FTCH_HTTP_PROXY")`) — no provider abstraction needed
9. `_open_persistent_page(pw, ...)` — asynccontextmanager, same as jobseek but without metrics
10. `navigate(page, url, config)` — same logic, remove metrics calls
11. `dismiss_overlays(page)` — function that removes cookie banners using OVERLAY_SELECTORS
12. `run_actions(page, actions)` — same dispatcher, remove metrics
13. `_execute_action(page, action, kind)` — support: remove, click, wait, evaluate,
    dismiss_overlays, repeat. Skip `paginate_collect` for now (advanced, add later).
14. `_execute_repeat(page, action)` — same as jobseek, but without cross-origin frame logic
    (simplify: just the basic repeat-click loop, skip the frame_selector path)
15. `safe_content(page)` — retry page.content() once on NavigationError

#### What to EXCLUDE
- All `from src import metrics` / `metrics.xxx.inc()` calls
- `_x_server_alive()`, `_resolve_headless()` — no Xvfb in job_ftch
- `_resolve_placeholders()` — UUID cookie injection (niche, skip)
- `paginate_collect` action type — too complex, skip for now
- `src.shared.proxy` imports — replace with simple env var

#### Import changes
- Replace `import structlog; log = structlog.get_logger()` → same (structlog is used)
- Remove all `from src.xxx` imports

#### Dependencies to add in pyproject.toml
Add to optional group `[browser]`:
```
playwright>=1.44.0
```
The installer note: `uv add playwright --optional browser && playwright install chromium`

### File to MODIFY: job_ftch/infrastructure/sources/monitors/dom.py

Current: static httpx-only with selectolax.
Upgrade to match jobseek's dom.py feature set.

Read the full current file first, then rewrite with these additions:

1. Add `from html.parser import HTMLParser` (replace selectolax dependency)
   — Use stdlib HTMLParser for `<a href>` extraction (same as jobseek)

2. Add `_build_url_matcher(url_filter) -> re.Pattern | None`
   — Compiles url_filter config (str or dict with "include" key) into regex

3. Replace `_extract_links()` with `_extract_links_static(html, base_url, url_matcher=None)`
   — When url_matcher provided: use regex instead of keyword filter
   — This enables non-English career pages (e.g. Russian, German)

4. Add `_extract_links_rendered(page, board_url, url_matcher=None) -> set[str]`
   — navigate(page, board_url, config), run_actions(page, actions)
   — Extract via JS: `page.evaluate("""() => Array.from(...)""")`
   — Filter with url_matcher or _JOB_KEYWORDS

5. Add `_paginate_urls(board_url, pagination, initial_urls, client, url_matcher) -> set[str]`
   — Reads: param_name or url_template, start, increment, max_pages from pagination dict
   — Fetches via httpx (not browser — browser pagination is advanced, skip for now)
   — Uses `fetch_page_text` from shared module
   — Breaks on empty response or no new URLs
   — Cap at MAX_URLS

6. Rewrite `discover(spec, client, auth=None)` to:
   ```
   config = spec.monitor_config
   render = config.get("render", False)
   url_matcher = _build_url_matcher(config.get("url_filter"))
   pagination = config.get("pagination")
   ```
   - If render=True: launch playwright, call _extract_links_rendered
   - If render=False: call _extract_links_static (existing behavior)
   - After link extraction: optionally call _paginate_urls
   - Apply MAX_URLS cap
   - Filter out board URL itself

7. Update `can_handle()` — add url_filter support

8. The `render: true` path needs playwright import guard:
   ```python
   try:
       from playwright.async_api import async_playwright
   except ImportError:
       raise RuntimeError("playwright is required for DOM monitor with render=true. "
                          "Install: uv sync --group browser && playwright install chromium")
   ```

9. Import `navigate`, `run_actions`, `BROWSER_KEYS` from `browser_utils` only when render=True
   (lazy import inside the if render: block to avoid hard playwright dep)

### File to MODIFY: pyproject.toml

1. Add `defusedxml>=0.7.1` to main dependencies (if not already added by XXE fix task).
2. Add optional group `[browser]`:
   ```toml
   [tool.uv.optional-dependencies]
   browser = ["playwright>=1.44.0"]
   ```
   Check current optional groups before modifying — add browser group without touching others.

---

## Phase 2: Additional ATS Monitors

### File to CREATE: job_ftch/infrastructure/sources/monitors/deel.py

Port from: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\deel.py
Read the full source file.

Adaptations:
- Replace `from src.core.monitors import DiscoveredJob, register` with
  `from job_ftch.application.registry import register_monitor`
  and `from job_ftch.infrastructure.sources.site_models import DiscoveredPostingPayload`
- Replace `DiscoveredJob(...)` with `DiscoveredPostingPayload(url=..., title=..., description=..., ...)`
- Replace structlog usage: `import structlog; log = structlog.get_logger()`
- Replace `register("deel", discover, cost=..., rich=True, can_handle=can_handle)` with
  `register_monitor("deel", discover, cost=20, rich=True, can_handle=can_handle)`
- Remove `from src.shared.truncation import truncated_rich_result` — implement inline
  (just slice the list to MAX_JOBS if needed, no jobseek watermark system)
- Keep the salary parsing helper `_parse_salary()`
- Keep `can_handle(url, client)` — checks for jobs.deel.com domain
- Keep the full paginated API fetch logic

Deel is a RICH monitor (returns full DiscoveredPostingPayload with descriptions).
Set cost=20 (cheaper than sitemap, uses API directly).

### File to CREATE: job_ftch/infrastructure/sources/monitors/softgarden.py

Port from: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\softgarden.py
Read the full source file.

Adaptations:
- Replace `from src.core.monitors import fetch_page_text, register` with
  `from job_ftch.application.registry import register_monitor` and
  `from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text`
- Replace `from src.shared.truncation import truncated_url_result` — implement inline
  (just slice to MAX_JOBS)
- Replace `register(...)` with `register_monitor("softgarden", discover, cost=70, rich=False, can_handle=can_handle)`
- `can_handle()` checks _PAGE_MARKERS in HTML
- Returns URL set (URL-only monitor, needs json-ld scraper)

### File to CREATE: job_ftch/infrastructure/sources/monitors/join.py

Port from: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\join.py
Read the full source file.

Adaptations:
- Replace `from src.core.monitors import fetch_page_text, register` with job_ftch equivalents
- Replace `from src.core.monitors.nextdata import discover as nextdata_discover` with
  `from job_ftch.infrastructure.sources.monitors.nextdata import discover as nextdata_discover`
- Replace `from src.shared.nextdata import extract_next_data, resolve_path` with
  `from job_ftch.infrastructure.sources.nextdata_utils import extract_next_data, resolve_path`
- Replace `register(...)` with `register_monitor("join", discover, cost=40, rich=False, can_handle=can_handle)`
- Join is URL-only (needs nextdata/json-ld scraper)

### File to CREATE: job_ftch/infrastructure/sources/monitors/eightfold.py

Port from: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\eightfold.py
Read the full source file first.

**Simplified version — sitemap-only mode only** (skip PCSX/watermark/incremental logic):
- Use the existing sitemap monitor's `discover()` as the backend
- Detect Eightfold by `*.eightfold.ai` domain + sitemap at `/careers/sitemap.xml`
- `can_handle()` checks for eightfold.ai hostname
- Returns URL set from sitemap (URL-only, needs json-ld scraper)
- Skip: `_pcsx`, `_watermark`, incremental fetching, watermark keys — too complex
- Set cost=30

Adaptations:
- Replace `from src.core.monitors.sitemap import discover as sitemap_discover` with
  `from job_ftch.infrastructure.sources.monitors.sitemap import discover as sitemap_discover`
- Replace `register(...)` with `register_monitor("eightfold", discover, cost=30, rich=False, can_handle=can_handle)`

---

## Phase 3: Register new monitors in __init__.py

### File to MODIFY: job_ftch/infrastructure/sources/monitors/__init__.py

Add the four new monitors to the `load_monitors()` function and import list:
- `from . import deel as deel`
- `from . import softgarden as softgarden`
- `from . import join as join`
- `from . import eightfold as eightfold`
- Add their module paths to the `for module_name in (...)` tuple inside `load_monitors()`

---

## Phase 4: Update example config and ADR

### File to MODIFY: config/sources.example.yaml

Add example entries for:
- career_site with render: true (for SPA sites)
- career_site with monitor: deel
- career_site with monitor: eightfold

Example additions (append to the sources list):
```yaml
  # SPA career site — requires Playwright render
  - type: career_site
    url: https://company.com/jobs
    source_name: company_spa
    monitor: dom
    monitor_config:
      render: true
      wait: domcontentloaded
      actions:
        - action: dismiss_overlays

  # Deel ATS board
  - type: career_site
    url: https://jobs.deel.com/acme
    source_name: deel_acme
    monitor: deel

  # Eightfold AI portal
  - type: career_site
    url: https://acme.eightfold.ai/careers
    source_name: eightfold_acme
    monitor: eightfold
```

---

## Verification

After implementation, run:

```
python -m pytest tests/ -q --tb=short 2>&1 | tail -10
```
Expected: same pass count as before (234 passed, 10 skipped).

```
python -m mypy job_ftch/infrastructure/sources/browser_utils.py \
               job_ftch/infrastructure/sources/monitors/dom.py \
               job_ftch/infrastructure/sources/monitors/deel.py \
               job_ftch/infrastructure/sources/monitors/softgarden.py \
               job_ftch/infrastructure/sources/monitors/join.py \
               job_ftch/infrastructure/sources/monitors/eightfold.py \
               --ignore-missing-imports 2>&1 | tail -5
```
Expected: 0 errors (or only missing playwright stubs which is acceptable).

---

## Strict Rules

1. Read each source file from jobseek COMPLETELY before adapting.
2. Read each job_ftch target file COMPLETELY before modifying.
3. NEVER import from `src.xxx` — all jobseek-specific imports must be replaced.
4. NEVER use `DiscoveredJob` — use `DiscoveredPostingPayload` from `site_models`.
5. NEVER import prometheus_client, metrics, or anything from jobseek's infra.
6. All new Python files must have `from __future__ import annotations` at the top.
7. Type hints required on all functions.
8. Use structlog throughout (not stdlib logging).
9. New monitors must call `register_monitor(name, factory, cost, rich, can_handle)`.
10. browser_utils.py must be importable WITHOUT playwright installed (lazy import inside functions).
