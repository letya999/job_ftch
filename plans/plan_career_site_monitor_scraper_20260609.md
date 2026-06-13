# Plan: Career Site Monitor/Scraper Architecture from jobseek

## Context

job_ftch currently has a `CareerSiteSource` backed by a `declarative.py` HTML parser that
does CSS-selector field mapping. This works for simple boards but cannot handle ATS APIs
(Greenhouse, Lever, Ashby, etc.), JS-rendered pages (__NEXT_DATA__), or XML sitemaps.

jobseek (C:\Users\User\a_projects\jobseek\apps\crawler\src\core\) has a production-proven
monitor/scraper split covering 30+ ATS platforms. We borrow their **architecture and
implementations**, adapt to job_ftch hexagonal layer rules, and keep job_ftch's existing
pipeline intact.

## Hard Constraints (must not break)

1. `domain/` imports only pydantic + stdlib. ZERO infra deps.
2. `application/` imports only `domain/` + stdlib + pydantic. No infra.
3. `SanitizeNode` is always first — unchanged.
4. `RawItem` remains the single output type of any source into the pipeline.
5. New types `DiscoveredPostingPayload` / `ScrapedPostingPayload` live ONLY in `infrastructure/sources/`.
6. All new monitors/scrapers are self-registered via decorators — zero `if/elif` dispatch in core.
7. `CareerSiteSpec` is extended non-breakingly (all new fields have defaults).
8. Existing `declarative.py` and `CareerSiteConfig` are kept as the `"dom"` scraper family
   (demoted from "main architecture" to "one strategy among many").

## New ADR

Create `docs/adr/021-career-site-monitor-scraper-split.md` documenting:
- Rationale: discovery (what exists) vs extraction (what is the content) are separate concerns
- `BoardMonitor` discovers URLs or full payloads
- `JobScraper` extracts content from individual URLs (only needed when monitor is URL-only)
- `DiscoveredPostingPayload` / `ScrapedPostingPayload` are infra-layer DTOs, not domain types
- Fallback chain: primary scraper -> fallback scraper list (tried in order on empty/failed result)
- `CareerSiteSpec.monitor="auto"` triggers `detect_monitor_type()` auto-detection
- Browser/Playwright enabled per-source via `monitor_config.render=true` or `scraper_config.render=true`
- All new code lives in `infrastructure/sources/monitors/` and `infrastructure/sources/scrapers/`

## Phase 1 — Contracts and registry extensions

### File: `job_ftch/application/contracts.py` (MODIFY)

Add two new protocols at the bottom (after existing protocols):

```python
class BoardMonitor(Protocol):
    """Discovers what jobs exist on a board.
    Returns DiscoveredPostingPayload (rich) or set[str] (URL-only).
    Defined as Protocol[Any] to stay infra-agnostic; concrete types in infra layer.
    """
    async def discover(self, spec: "CareerSiteSpec", http: Any) -> Any: ...

class JobScraper(Protocol):
    """Extracts structured content from a single job URL."""
    async def scrape(self, url: str, config: dict, http: Any) -> Any: ...
```

Note: use `from __future__ import annotations` and TYPE_CHECKING guards so no infra type
leaks into application layer.

### File: `job_ftch/application/registry.py` (MODIFY)

Add monitor/scraper registry alongside existing source/sink/store registries.

New dataclasses (in registry.py, not in infra — they are pure config):

```python
@dataclass
class MonitorEntry:
    name: str
    cost: int          # lower = cheaper = tried first in auto-detect
    rich: bool         # True = returns full payload, no scraper needed
    factory: Callable  # (spec, http, auth) -> monitor instance OR discover coroutine
    can_handle: Callable | None = None  # async (url, client) -> dict | None

@dataclass
class ScraperEntry:
    name: str
    factory: Callable  # (config, http) -> scraper instance OR scrape coroutine
    can_handle: Callable | None = None  # (list[str]) -> dict | None  (static HTML probe)
    needs_browser: bool = False
```

New registry lists: `_MONITOR_REGISTRY: list[MonitorEntry]`, `_SCRAPER_REGISTRY: dict[str, ScraperEntry]`

New functions:
- `register_monitor(name, factory, cost, rich, can_handle=None)` — registers and keeps sorted by cost
- `register_scraper(name, factory, can_handle=None, needs_browser=False)`
- `resolve_monitor(name: str) -> MonitorEntry`
- `resolve_scraper(name: str) -> ScraperEntry`
- `detect_monitor_type(url, client, pw=None) -> tuple[str, dict] | None`
  — iterates `_MONITOR_REGISTRY` sorted by cost, calls `can_handle()` on each, returns first match
- `all_monitor_names() -> frozenset[str]`
- `rich_monitor_names() -> frozenset[str]`

## Phase 2 — Infrastructure DTOs

### File: `job_ftch/infrastructure/sources/site_models.py` (CREATE NEW)

Pure dataclasses (no pydantic, no domain deps):

```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(slots=True)
class DiscoveredPostingPayload:
    """Output of a BoardMonitor. Either URL-only or rich (full fields)."""
    url: str
    title: str | None = None
    description: str | None = None   # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    language: str | None = None
    localizations: dict | None = None
    extras: dict | None = None
    metadata: dict | None = None

@dataclass(slots=True)
class ScrapedPostingPayload:
    """Output of a JobScraper. Same shape as DiscoveredPostingPayload minus url."""
    title: str | None = None
    description: str | None = None   # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    language: str | None = None
    extras: dict | None = None
    metadata: dict | None = None

@dataclass
class MonitorResult:
    """Normalized result from a monitor run."""
    urls: set[str] = field(default_factory=set)
    payloads_by_url: dict[str, DiscoveredPostingPayload] | None = None
    metadata_updates: dict | None = None
    hybrid: bool = False     # partial-rich: some URLs have data, others don't
    truncated: bool = False  # hit MAX_JOBS cap; pipeline skips tombstone logic
    filtered_count: int = 0
```

### File: `job_ftch/infrastructure/sources/site_utils.py` (CREATE NEW)

Utility functions ported/adapted from jobseek:

- `normalize_monitor_result(raw)` — normalizes list[DiscoveredPostingPayload] | set[str] | MonitorResult | tuple[set[str], str|None] -> MonitorResult
- `apply_url_filter(result, url_filter_config)` -> MonitorResult  (regex include/exclude)
- `apply_url_transform(result, url_transform_config)` -> MonitorResult  (regex find/replace)
- `enrich_description(payload)` — appends extras (skills, responsibilities, qualifications) to description HTML (port from jobseek scrapers/__init__.py::enrich_description)
- `payload_to_raw_item(payload, spec, source_name)` -> RawItem  (uses existing raw_item_factory)

## Phase 3 — Extended CareerSiteSpec

### File: `job_ftch/domain/source_spec.py` (MODIFY)

Extend `CareerSiteSpec` — all fields have defaults, fully backward-compatible:

```python
class CareerSiteSpec(BaseSourceSpec):
    type: Literal["career_site"] = "career_site"
    url: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None
    # New fields (Phase 3):
    monitor: str | None = "auto"   # registered monitor name, or "auto" for auto-detect
    monitor_config: dict = Field(default_factory=dict)  # passed to monitor
    scraper: str | None = None     # registered scraper name; None = auto from monitor
    scraper_config: dict = Field(default_factory=dict)  # passed to scraper
    scraper_fallback: list[str] = Field(default_factory=list)  # fallback scraper chain
    detail_limit: int | None = None  # max detail pages to scrape (None = unlimited)
    url_filter: str | dict | None = None   # regex or {include, exclude}
    url_transform: dict | None = None      # {find, replace} regex rewrite
```

## Phase 4 — New CareerSiteSource runtime

### File: `job_ftch/infrastructure/sources/career_site_source.py` (CREATE NEW, replaces old career_site.py logic)

Class `CareerSiteSource`:
- `__init__(self, spec: CareerSiteSpec, http_client, auth: AuthProvider)`
- `async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]`

Logic:
```
1. Resolve monitor: spec.monitor == "auto" -> detect_monitor_type(spec.url, client)
                    spec.monitor is name -> resolve_monitor(name)
2. Run monitor.discover(spec, client) -> MonitorResult (via normalize_monitor_result)
3. Apply url_filter, url_transform from spec
4. If result.payloads_by_url (rich monitor):
     For each DiscoveredPostingPayload -> payload_to_raw_item -> yield RawItem
5. If URL-only (result.urls):
     Resolve scraper: spec.scraper or default from monitor, or "json-ld" fallback
     For each url (up to spec.detail_limit):
       Try primary scraper -> ScrapedPostingPayload
       If empty/failed and spec.scraper_fallback:
         Try each fallback in order until one returns content
       merge payload + url -> DiscoveredPostingPayload -> payload_to_raw_item -> yield RawItem
6. Emit run stats: monitored, rich_emitted, scraped, fallback_used, truncated
```

Keep existing `career_site.py` (declarative CSS-selector approach) as the `"declarative"` monitor
registration. The new source is registered as the `"career_site"` source type in the registry.

### File: `job_ftch/infrastructure/sources/__init__.py` (MODIFY)

Re-export `CareerSiteSource` and update source registry to use new runtime for `"career_site"` type.

## Phase 5 — Port monitors from jobseek

Create directory: `job_ftch/infrastructure/sources/monitors/`
Create `job_ftch/infrastructure/sources/monitors/__init__.py` — trigger registration by importing all monitors.

Port these monitors (adapt imports, remove DB/Redis/R2 deps, use job_ftch's httpx client):

### `monitors/greenhouse.py` (PORT from jobseek monitors/greenhouse.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\greenhouse.py
- Rich monitor (returns full DiscoveredPostingPayload with description HTML)
- `can_handle`: domain check + page HTML scan + slug probe
- Returns list[DiscoveredPostingPayload]
- `register_monitor("greenhouse", ..., cost=10, rich=True, can_handle=can_handle)`
- NOTE: existing `infrastructure/sources/api/greenhouse.py` (OfficialAPISource subclass) is a
  different path; this new monitor is for auto-detection of Greenhouse boards on arbitrary URLs.
  Both can coexist — new monitor registered as "greenhouse_board", existing as "greenhouse_api".

### `monitors/lever.py` (PORT from jobseek monitors/lever.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\lever.py
- Rich monitor (Lever v0 public JSON API: api.lever.co/v0/postings/{token}?mode=json)
- `can_handle`: domain detection + slug probe
- Returns list[DiscoveredPostingPayload]
- `register_monitor("lever_board", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/sitemap.py` (PORT from jobseek monitors/sitemap.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\sitemap.py
- URL-only monitor — discovers job URLs from XML sitemap
- Handles sitemapindex (recursive), namespace variants, UTM stripping
- `can_handle`: robots.txt probe + common /sitemap.xml, /sitemap_jobs.xml paths
- Returns tuple[set[str], str | None] (URLs, discovered sitemap URL)
- `register_monitor("sitemap", ..., cost=20, rich=False, can_handle=can_handle)`
- Keep jobseek's `_SITEMAP_HEADERS` override (self-identifying crawler UA)

### `monitors/dom.py` (PORT from jobseek monitors/dom.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\dom.py
- URL-only — extracts job links from HTML page using configurable CSS selectors
- `can_handle`: fetch page, look for link patterns
- Returns set[str]
- `register_monitor("dom", ..., cost=50, rich=False, can_handle=can_handle)`

### `monitors/nextdata.py` (PORT from jobseek monitors/nextdata.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\nextdata.py
- Handles Next.js `__NEXT_DATA__` boards. Can be rich when `fields` configured.
- `can_handle`: check for `__NEXT_DATA__` script tag, probe JSON path
- `register_monitor("nextdata", ..., cost=30, rich=False, can_handle=can_handle)`
  (rich when fields configured: `is_rich_monitor("nextdata", config)` check)

### `monitors/ashby.py` (PORT from jobseek monitors/ashby.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\ashby.py
- Rich monitor (Ashby public API: api.ashbyhq.com/posting-api/job-board/{token})
- Includes compensation data
- `register_monitor("ashby", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/workday.py` (PORT from jobseek monitors/workday.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\workday.py
- Workday REST API (large enterprise boards like Google, Microsoft, Salesforce)
- `register_monitor("workday", ..., cost=15, rich=False, can_handle=can_handle)`
  (URL-only — detail pages need scraper)

### `monitors/smartrecruiters.py` (PORT from jobseek monitors/smartrecruiters.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\smartrecruiters.py
- SmartRecruiters public API
- `register_monitor("smartrecruiters", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/breezy.py` (PORT from jobseek monitors/breezy.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\breezy.py
- Breezy HR JSON API ({slug}.breezy.hr/json)
- `register_monitor("breezy", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/recruitee.py` (PORT from jobseek monitors/recruitee.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\recruitee.py
- Recruitee public API ({slug}.recruitee.com/api/offers)
- `register_monitor("recruitee", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/personio.py` (PORT from jobseek monitors/personio.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\personio.py
- Personio XML feed ({slug}.jobs.personio.de/xml)
- Rich monitor (XML contains full job data)
- `register_monitor("personio", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/rss.py` (ADAPT from jobseek monitors/rss.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\rss.py
- RSS/Atom feed monitor with presets: successfactors, teamtailor, generic
- Different from existing `RSSFeedSource` — this is a monitor for career board RSS specifically
- `register_monitor("rss_board", ..., cost=25, rich=False, can_handle=can_handle)`

### `monitors/rippling.py` (PORT from jobseek monitors/rippling.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\rippling.py
- Rippling ATS API (api.rippling.com/platform/api/ats/v1/board/{slug}/jobs)
- `register_monitor("rippling", ..., cost=10, rich=True, can_handle=can_handle)`

### `monitors/workable.py` (PORT from jobseek monitors/workable.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\workable.py
- Workable API (apply.workable.com/{token}/jobs)
- `register_monitor("workable", ..., cost=10, rich=True, can_handle=can_handle)`

## Phase 6 — Port scrapers from jobseek

Create directory: `job_ftch/infrastructure/sources/scrapers/`
Create `job_ftch/infrastructure/sources/scrapers/__init__.py` — trigger registration by importing all scrapers.

### `scrapers/jsonld.py` (PORT from jobseek scrapers/jsonld.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\jsonld.py
- Extracts schema.org/JobPosting from `<script type="application/ld+json">` blocks
- Handles: PascalCase key normalization, control chars in JSON, 403-retry with backoff
- `can_handle(htmls: list[str]) -> dict | None` — static probe
- `parse_html(html, config) -> ScrapedPostingPayload`
- `async scrape(url, config, http, pw=None) -> ScrapedPostingPayload`
- When `config.get("render")`: use Playwright render (optional import)
- `register_scraper("json-ld", scrape, can_handle=can_handle)`

### `scrapers/embedded.py` (PORT from jobseek scrapers/embedded.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\embedded.py
- Handles `<script id="...">` blocks, variable assignments, regex patterns
- Uses jmespath for path resolution (add `jmespath` to optional [site_scrapers] extras)
- Config-driven field mapping
- `register_scraper("embedded", scrape, can_handle=can_handle)`

### `scrapers/nextdata.py` (PORT from jobseek scrapers/nextdata.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\nextdata.py
- Thin wrapper around embedded for `__NEXT_DATA__` specifically
- `register_scraper("nextdata", scrape, can_handle=can_handle)`

### `scrapers/dom.py` (PORT from jobseek scrapers/dom.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\dom.py
- Step-based config: list of CSS selector steps to locate and extract job fields
- Most flexible fallback — works on any custom HTML career page
- `register_scraper("dom", scrape, can_handle=can_handle)`

### `scrapers/workday.py` (PORT from jobseek scrapers/workday.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\workday.py
- Workday detail page extractor (pairs with workday monitor)
- `register_scraper("workday", scrape)`

### `scrapers/smartrecruiters.py` (PORT from jobseek scrapers/smartrecruiters.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\smartrecruiters.py
- SmartRecruiters detail page (for hybrid boards)
- `register_scraper("smartrecruiters", scrape)`

### `scrapers/workable.py` (PORT from jobseek scrapers/workable.py)
- Source: C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\workable.py
- Workable detail page
- `register_scraper("workable", scrape)`

## Phase 7 — Fallback chain and scraper selection

In `career_site_source.py`, implement `_resolve_scraper_chain(spec, monitor_name, monitor_config)`:

```
1. If spec.scraper is set: primary = spec.scraper, fallbacks = spec.scraper_fallback
2. If monitor is rich: no scraper needed (return None)
3. Auto-resolve from monitor name:
   "sitemap" -> primary="json-ld", fallbacks=["embedded", "nextdata", "dom"]
   "dom" -> primary="json-ld", fallbacks=["embedded", "dom"]
   "nextdata" -> primary="nextdata", fallbacks=["json-ld", "embedded"]
   "workday" -> primary="workday", fallbacks=["json-ld"]
   "smartrecruiters" -> primary="smartrecruiters", fallbacks=["json-ld"]
4. Default: primary="json-ld", fallbacks=["embedded", "nextdata", "dom"]
```

Fallback logic in `_scrape_with_fallback(url, scraper_chain, http)`:
```
For each scraper in chain:
  Try scrape(url, config, http)
  If result has title or description: return result + log which scraper succeeded
  If empty: try next
Return empty ScrapedPostingPayload + log all_scrapers_empty warning
```

## Phase 8 — RunSummary observability counters

### File: `job_ftch/domain/models.py` (MODIFY — RunSummary)

Add optional counter fields to `RunSummary` (all default to 0):
```python
monitored: int = 0           # URLs/items discovered by monitor
rich_emitted: int = 0        # items from rich monitors (no scraper needed)
scraped: int = 0             # items processed by scraper
scrape_fallback_used: int = 0  # times fallback scraper was triggered
source_partial: bool = False   # at least one monitor was truncated
monitor_truncated: int = 0     # count of truncated monitor runs
```

## Phase 9 — Tests

### `tests/sources/monitors/test_greenhouse_monitor.py`
- Fixture: `tests/fixtures/monitors/greenhouse_response.json` (copy from jobseek or create minimal)
- Test: `discover()` maps jobs correctly to `DiscoveredPostingPayload`
- Test: `can_handle()` detects boards.greenhouse.io URLs without HTTP
- Test: `can_handle()` scans HTML page for embedded board token

### `tests/sources/monitors/test_sitemap_monitor.py`
- Fixture: `tests/fixtures/monitors/sample_sitemap.xml`
- Test: URL extraction, UTM stripping, sitemapindex recursion

### `tests/sources/monitors/test_lever_monitor.py`
- Fixture: `tests/fixtures/monitors/lever_response.json`
- Test: discover() maps to DiscoveredPostingPayload

### `tests/sources/scrapers/test_jsonld_scraper.py`
- Fixture: sample HTML pages with schema.org/JobPosting blocks
- Test: `parse_html()` extracts title, description, locations, salary
- Test: `can_handle()` majority rule (requires >= half of pages)
- Test: PascalCase key normalization (Cornerstone OnDemand pattern)
- Test: control chars in JSON (escape and retry)

### `tests/sources/scrapers/test_embedded_scraper.py`
- Fixture: HTML with `__NEXT_DATA__` and custom `window.__DATA__`
- Test: jmespath field extraction

### `tests/sources/test_career_site_source.py`
- Test: rich monitor -> RawItem emitted without scraper
- Test: URL-only monitor -> scraper -> RawItem
- Test: fallback chain triggers on empty primary scraper
- Test: url_filter applied correctly
- Test: truncated=True sets source_partial in RunSummary
- Test: detail_limit respected

### `tests/sources/monitors/test_monitor_registry.py`
- Test: detect_monitor_type returns lowest-cost match
- Test: all registered monitors have valid cost values
- Test: rich_monitor_names() returns correct set

## Phase 10 — pyproject.toml extras

Add optional extras group `[site_scrapers]` to `pyproject.toml`:
```toml
[project.optional-dependencies]
site_scrapers = ["jmespath>=1.0", "feedparser>=6.0"]
```

`jmespath` is needed by `embedded.py` scraper.
`feedparser` already in `[feeds]` — reuse or add to site_scrapers.

## Phase 11 — ADR and doc updates

1. Write `docs/adr/021-career-site-monitor-scraper-split.md` (see Phase 0 outline above)
2. Update `docs/architecture.md`: add monitor/scraper section under "Source layer"
3. Update `docs/tech_stack.md`: add `jmespath` to scraper deps

## Serena memory update

After implementation, update `.serena/memories/core.md`:
- Add monitor/scraper infra layer to source map
- Note that `career_site` type now uses monitor->scraper orchestration
- Note auto-detect available via `monitor="auto"` in CareerSiteSpec

## File summary

### NEW files:
- `job_ftch/infrastructure/sources/site_models.py` — DTOs: DiscoveredPostingPayload, ScrapedPostingPayload, MonitorResult
- `job_ftch/infrastructure/sources/site_utils.py` — normalize_monitor_result, payload_to_raw_item, enrich_description, url_filter, url_transform
- `job_ftch/infrastructure/sources/career_site_source.py` — new runtime: resolve monitor, run monitor, run scraper chain, emit RawItem
- `job_ftch/infrastructure/sources/monitors/__init__.py` — monitor registry loader (imports all monitors)
- `job_ftch/infrastructure/sources/monitors/greenhouse.py`
- `job_ftch/infrastructure/sources/monitors/lever.py`
- `job_ftch/infrastructure/sources/monitors/sitemap.py`
- `job_ftch/infrastructure/sources/monitors/dom.py`
- `job_ftch/infrastructure/sources/monitors/nextdata.py`
- `job_ftch/infrastructure/sources/monitors/ashby.py`
- `job_ftch/infrastructure/sources/monitors/workday.py`
- `job_ftch/infrastructure/sources/monitors/smartrecruiters.py`
- `job_ftch/infrastructure/sources/monitors/breezy.py`
- `job_ftch/infrastructure/sources/monitors/recruitee.py`
- `job_ftch/infrastructure/sources/monitors/personio.py`
- `job_ftch/infrastructure/sources/monitors/rss_board.py`
- `job_ftch/infrastructure/sources/monitors/rippling.py`
- `job_ftch/infrastructure/sources/monitors/workable.py`
- `job_ftch/infrastructure/sources/scrapers/__init__.py` — scraper registry loader
- `job_ftch/infrastructure/sources/scrapers/jsonld.py`
- `job_ftch/infrastructure/sources/scrapers/embedded.py`
- `job_ftch/infrastructure/sources/scrapers/nextdata.py`
- `job_ftch/infrastructure/sources/scrapers/dom.py`
- `job_ftch/infrastructure/sources/scrapers/workday.py`
- `job_ftch/infrastructure/sources/scrapers/smartrecruiters.py`
- `job_ftch/infrastructure/sources/scrapers/workable.py`
- `docs/adr/021-career-site-monitor-scraper-split.md`
- `tests/sources/monitors/test_greenhouse_monitor.py`
- `tests/sources/monitors/test_sitemap_monitor.py`
- `tests/sources/monitors/test_lever_monitor.py`
- `tests/sources/scrapers/test_jsonld_scraper.py`
- `tests/sources/scrapers/test_embedded_scraper.py`
- `tests/sources/test_career_site_source.py`
- `tests/sources/monitors/test_monitor_registry.py`
- `tests/fixtures/monitors/greenhouse_response.json`
- `tests/fixtures/monitors/lever_response.json`
- `tests/fixtures/monitors/sample_sitemap.xml`
- `tests/fixtures/scrapers/jsonld_sample.html`

### MODIFIED files:
- `job_ftch/application/contracts.py` — add BoardMonitor, JobScraper protocols
- `job_ftch/application/registry.py` — add MonitorEntry, ScraperEntry, register_monitor, register_scraper, resolve_monitor, resolve_scraper, detect_monitor_type
- `job_ftch/domain/source_spec.py` — extend CareerSiteSpec with monitor/scraper strategy fields
- `job_ftch/domain/models.py` — add RunSummary counters: monitored, rich_emitted, scraped, scrape_fallback_used, source_partial, monitor_truncated
- `job_ftch/infrastructure/sources/__init__.py` — register CareerSiteSource for "career_site" type
- `docs/architecture.md` — document monitor/scraper layer
- `docs/tech_stack.md` — add jmespath
- `pyproject.toml` — add [site_scrapers] extras
- `.serena/memories/core.md` — update source map

### UNCHANGED files (critical):
- `job_ftch/domain/models.py` — RawItem, Job unchanged (only RunSummary counters added)
- `job_ftch/infrastructure/sources/declarative.py` — kept as "declarative" scraper strategy
- `job_ftch/infrastructure/sources/composite.py` — unchanged
- `job_ftch/application/pipeline.py` — unchanged
- All nodes (SanitizeNode etc.) — unchanged

## jobseek source references

All source files to read when implementing:
- Monitors: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\*.py`
- Scrapers: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\*.py`
- Monitor registry: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\__init__.py`
- Scraper registry: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrapers\__init__.py`
- Dispatcher: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitor.py`
- Scrape dispatcher: `C:\Users\User\a_projects\jobseek\apps\crawler\src\core\scrape.py`
- Shared utils: `C:\Users\User\a_projects\jobseek\apps\crawler\src\shared\`

## Adaptation rules when porting from jobseek

1. Replace `from src.*` imports with `job_ftch.*` equivalents
2. Replace `DiscoveredJob` with `DiscoveredPostingPayload`
3. Replace `JobContent` with `ScrapedPostingPayload`
4. Remove all DB/Redis/R2/Postgres references
5. Remove `artifact_dir` debug-save logic (not needed in job_ftch)
6. Replace `src.shared.http.client_for` with job_ftch's httpx client (plain `httpx.AsyncClient`)
7. Replace `src.shared.browser.render` with job_ftch's `BypassStrategy` pattern
   (guard with `try: from playwright...` optional import, same as existing browser stub)
8. Replace `structlog.get_logger()` with `logging.getLogger("job_ftch.monitors.<name>")`
9. Replace `from src.core.monitors import register` with `from job_ftch.application.registry import register_monitor`
10. Replace `from src.core.scrapers import register` with `from job_ftch.application.registry import register_scraper`
11. `BoardGoneError` -> keep as local exception in monitors/__init__.py (or re-raise as `SourceError`)
12. `_save_raw` / `artifact_dir` logic -> remove entirely
13. Keep the `cost=` values as-is from jobseek (they reflect real-world request cost)
14. Keep `can_handle` detection logic exactly as-is (it's well-tested in production)
15. Keep `truncated_rich_result` pattern: when jobs > MAX_JOBS, set `MonitorResult.truncated=True`

## Quality gates (all must pass before commit)

- `mypy --strict job_ftch/` — 0 errors
- `ruff check job_ftch/` — 0 violations
- `pytest tests/sources/` — all new tests pass
- `grep -r "from infrastructure" job_ftch/domain/ job_ftch/application/ job_ftch/nodes/ job_ftch/sinks/` — empty result
- `grep -r "from src\." job_ftch/` — empty result (no leftover jobseek imports)
- `pytest tests/` — existing 180+ tests still pass (no regressions)
