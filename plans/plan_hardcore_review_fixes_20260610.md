# Plan: Fix all hardcore-review findings
Date: 2026-06-10

## Context

Hardcore review of last 5 commits + uncommitted files found 16 issues split across:
- BLOCKER: untracked files imported from committed code
- HIGH: layer violations, resource leaks, race conditions, inverted cost ordering
- MEDIUM: undeclared attributes, dead code, weak protocol types, shared-state mutation
- LOW: naming, import placement, dual-state ambiguity, complexity

Current state:
- `ruff check`: all pass
- `mypy`: 150 errors in 37 files (many in new monitors/bypass files)
- `pytest`: 1 failed (test_outputs.py), 254 passed

Goal: 0 ruff errors, 0 mypy errors, 0 pytest failures, full coverage for new code.

---

## Group 1 — BLOCKER: Git tracking integrity

### 1.1 Commit or gate all untracked files that are imported [x]

Files that exist on disk (??  status) but are imported by tracked/modified __init__.py files:
- `job_ftch/infrastructure/bypass/adaptive.py` — imported by `bypass/__init__.py`
- `job_ftch/infrastructure/bypass/cloak_bypass.py` — imported by `bypass/__init__.py`
- `job_ftch/infrastructure/bypass/curl_bypass.py` — imported by `bypass/__init__.py`
- `job_ftch/infrastructure/sources/monitors/api_sniffer.py` — imported by `monitors/__init__.py`

**Action**: Stage and include all four files in the fix commit. They are already implemented
and import-tested locally. This is the minimum fix to make the repo self-consistent.

Also stage and commit these untracked test files (they cover the new bypass code):
- `tests/test_bypass.py`
- `tests/test_career_site_generic.py`

And the ADR documents:
- `docs/adr/022-cloakbrowser-advanced-bypass.md`
- `docs/adr/023-adaptive-scraping-escalation.md`

**DO NOT commit**:
- `*.jsonl` result files (add to .gitignore)
- `JOBSEEK_DEEP_DIVE.md`, `JOBSEEK_MEGAGUIDE.md` (dev artifacts, add to .gitignore)
- `plans/` directory (add to .gitignore)
- `config/test_*.yaml` files (add to .gitignore)

### 1.2 Update .gitignore [x]

Add to `.gitignore`:
```
# Test run artifacts
*.jsonl
adaptive_results.jsonl
getmatch_*.jsonl
global_test_results.jsonl

# Dev artifacts / notes
JOBSEEK_*.md
plans/

# Test configs
config/test_*.yaml
```

---

## Group 2 — HIGH: Architecture violations

### 2.1 Remove infra imports from application/builder.py

**File**: `job_ftch/application/builder.py:46-47`

These two lines violate the module boundary rule (application/ must not import from infrastructure/):
```python
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider      # REMOVE
from job_ftch.infrastructure.sources.composite import CompositeSource  # REMOVE
```

**Fix strategy**:
- Remove the direct imports
- Use TYPE_CHECKING guard if type annotations require them:
  ```python
  if TYPE_CHECKING:
      from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
      from job_ftch.infrastructure.sources.composite import CompositeSource
  ```
- If the builder actually INSTANTIATES these classes at runtime (not just type-annotates),
  the instantiation must move to the factory method signature or be deferred.
- Look at how builder.py uses them: if it calls `EnvAuthProvider()` or `CompositeSource(...)`,
  those calls should be moved to a factory in `infrastructure/` that builder.py accepts
  as injected arguments (AuthProvider protocol, Source protocol — both defined in application/contracts.py).
- The builder should accept `auth: AuthProvider` (the protocol, defined in application/) not the concrete class.

### 2.2 Move detect_monitor_type() out of application/registry.py

**File**: `job_ftch/application/registry.py` — function `detect_monitor_type()`

The function does real HTTP requests — this is an infrastructure concern, not a registry concern.

**Fix strategy**:
- Move `detect_monitor_type()` to a new location: `job_ftch/infrastructure/sources/monitor_detector.py`
- In `registry.py`, keep only the pure registry function: `get_all_monitor_entries() -> list[MonitorEntry]`
- Update `CareerSiteSource.fetch()` in `career_site_source.py` to call the new location directly
  (since CareerSiteSource is already in infrastructure/, this is fine)
- Remove the `detect_monitor_type` import from registry.py in career_site_source.py

---

## Group 3 — HIGH: Bug fixes

### 3.1 Fix CurlHttpxAdapter resource leak (unawaited close)

**File**: `job_ftch/infrastructure/bypass/curl_bypass.py:38-39`

```python
# CURRENT (BUG):
async def __aexit__(self, *args, **kwargs):
    self._sess.close()  # coroutine never awaited — leak!

# FIX:
async def __aexit__(self, *args, **kwargs: object) -> None:
    await self._sess.close()
```

Also remove the no-op on line 44:
```python
# REMOVE THIS LINE (no-op):
resp.raise_for_status = resp.raise_for_status
```

Also fix mypy errors in this file:
- Add type annotation for `session`: `session: AsyncSession`
- Use a proper `BrowserType` Literal for `impersonate` or cast the string
- Add return type annotations to all methods in `CurlHttpxAdapter`

### 3.2 Fix api_sniffer XHR capture race condition

**File**: `job_ftch/infrastructure/sources/monitors/api_sniffer.py`

Current pattern has race condition — `response.json()` called after event loop tick when
resource may be evicted:

```python
# CURRENT (RACE CONDITION):
async def capture_response(response: Any) -> None:
    ...
    data = await response.json()  # may fail if page navigated away

page.on("response", lambda response: response_tasks.append(
    asyncio.create_task(capture_response(response))
))
await asyncio.sleep(settle_seconds)  # fixed 4s hardcoded
await asyncio.gather(*response_tasks, return_exceptions=True)
```

**Fix**: Read response body immediately in the handler (synchronously in the same task),
wrap in try/except for non-JSON/evicted responses, replace fixed sleep with
`page.wait_for_load_state("networkidle")` with fallback:

```python
async def capture_response(response: Any) -> None:
    try:
        headers = await response.all_headers()
        content_type = headers.get("content-type", "")
        if "json" not in content_type.lower() and not _API_HINT_RE.search(response.url):
            return
        # Read immediately while resource is hot
        data = await response.json()
        response_payloads.append((response.url, data))
    except Exception:
        return  # resource evicted, non-JSON, redirect — skip silently

# Register as async handler directly (not via create_task):
page.on("response", lambda r: asyncio.ensure_future(capture_response(r)))

# Replace fixed sleep:
try:
    await page.wait_for_load_state("networkidle", timeout=10_000)
except Exception:
    await asyncio.sleep(settle_seconds)  # fallback for polling-heavy SPAs
```

Also remove `response_tasks` list and `asyncio.gather` — they are no longer needed.

---

## Group 4 — HIGH: Cost ordering inversion

### 4.1 Fix monitor cost values

**File**: `job_ftch/infrastructure/sources/monitors/api_sniffer.py` (last line)
**File**: `job_ftch/infrastructure/sources/monitors/dom.py` (last line)

Current values are INVERTED (api_sniffer=70 cheaper than dom=100, but api_sniffer uses full browser):

```python
# CURRENT (WRONG):
register_monitor("api_sniffer", discover, cost=70, ...)  # full browser!
register_monitor("dom", discover, cost=100, ...)          # HTTP only

# FIX — cost = relative resource cost, lower = try first:
register_monitor("dom", discover, cost=50, ...)           # HTTP only, cheapest
register_monitor("api_sniffer", discover, cost=200, ...)  # full browser, expensive
```

Check and adjust costs for ALL monitors to be consistent:
- HTTP-only monitors (dom, rss_board, sitemap, greenhouse, lever, etc.): cost 10-100
- API monitors with pagination: cost 50-150
- Browser-required monitors (api_sniffer, monitors that set render=True): cost 150-300

The cost ordering determines auto-detect order. Cheaper = simpler = tried first.

---

## Group 5 — MEDIUM: Class design and dead code

### 5.1 Initialize bypass_strategy in __init__

**File**: `job_ftch/infrastructure/sources/career_site_source.py`

`self.bypass_strategy` is only set inside `fetch()` but used in `_try_escalate_bypass()`.
Any call to `_try_escalate_bypass()` before `fetch()` raises AttributeError.

**Fix**: Initialize in `__init__`:
```python
def __init__(self, ...):
    ...
    self.bypass_strategy: Any = None  # initialized in fetch()
```

Or better: make `_try_escalate_bypass` accept the strategy as a parameter instead of
accessing it via self, removing the temporal coupling entirely.

### 5.2 Remove dead code: load_monitors() and load_scrapers()

**File**: `job_ftch/infrastructure/sources/monitors/__init__.py`
**File**: `job_ftch/infrastructure/sources/scrapers/__init__.py`

`load_monitors()` and `load_scrapers()` functions are defined but never called.
Registration already happens via the top-level `from . import X as X` statements.

**Fix**: Delete both functions entirely. They are dead code that creates confusion about
how registration works.

---

## Group 6 — MEDIUM: Type safety

### 6.1 Fix Protocol return types (Any -> concrete types)

**File**: `job_ftch/application/contracts.py`

```python
# CURRENT (weak):
class BoardMonitor(Protocol):
    async def discover(self, spec: CareerSiteSpec, http: Any) -> Any: ...

class JobScraper(Protocol):
    async def scrape(self, url: str, config: dict, http: Any) -> Any: ...

# FIX (import from infra is not allowed in application/ layer — use TYPE_CHECKING):
# These protocols should use the domain types only, OR reference infra types via forward refs.
# Since MonitorResult and ScrapedPostingPayload are in infrastructure/sources/site_models.py,
# they cannot be directly referenced from application/contracts.py without layer violation.
#
# Solution: Move MonitorResult and ScrapedPostingPayload to domain/ layer,
# OR keep protocols with Any return and add a note explaining the constraint,
# OR use TypeVar with bound Protocol.
#
# Recommended: Move MonitorResult and ScrapedPostingPayload to
# job_ftch/domain/site_models.py — they contain no infra imports (only stdlib + dataclasses).
# Then application/contracts.py can import them legally.
```

**Implementation steps**:
1. Move `MonitorResult`, `DiscoveredPostingPayload`, `ScrapedPostingPayload` from
   `job_ftch/infrastructure/sources/site_models.py` to `job_ftch/domain/site_models.py`
2. Update all imports: everywhere that imported from `infrastructure/sources/site_models`
   now imports from `domain/site_models`
3. Update `contracts.py` to use the concrete return types:
   ```python
   from job_ftch.domain.site_models import MonitorResult, ScrapedPostingPayload
   
   class BoardMonitor(Protocol):
       async def discover(self, spec: CareerSiteSpec, http: Any) -> MonitorResult: ...
   
   class JobScraper(Protocol):
       async def scrape(self, url: str, config: dict, http: Any) -> ScrapedPostingPayload | None: ...
   ```

### 6.2 Fix register_source_v2 naming

**File**: `job_ftch/application/registry.py`

Rename `register_source_v2` to `register_source_spec` throughout codebase:
```python
# RENAME: register_source_v2 -> register_source_spec
```

Search all files for `register_source_v2` and replace with `register_source_spec`.
Files to update:
- `job_ftch/application/registry.py` (definition)
- `job_ftch/infrastructure/sources/career_site_source.py` (usage)
- `job_ftch/infrastructure/sources/declarative.py` (usage — check if used)
- Any other files using it

---

## Group 7 — MEDIUM: Shared state mutation

### 7.1 Fix dataclass mutation in apply_url_transform

**File**: `job_ftch/infrastructure/sources/site_utils.py:95`

`DiscoveredPostingPayload` with `slots=True` is mutated in-place — shared reference issue.

```python
# CURRENT (mutates in-place):
payload.url = new_url
transformed_payloads[new_url] = payload

# FIX (create new instance):
import dataclasses
new_payload = dataclasses.replace(payload, url=new_url)
transformed_payloads[new_url] = new_payload
```

---

## Group 8 — LOW: Code quality

### 8.1 Move httpx import to module level

**File**: `job_ftch/infrastructure/sources/career_site_source.py`

```python
# REMOVE from inside _try_escalate_bypass():
import httpx

# ADD to module-level imports (already likely imported transitively):
import httpx
```

### 8.2 Fix payloads_by_url None vs {} (optional cleanup)

**File**: `job_ftch/infrastructure/sources/site_models.py`

Change `payloads_by_url` default from `None` to empty dict:
```python
# CURRENT:
payloads_by_url: dict[str, DiscoveredPostingPayload] | None = None

# FIX:
payloads_by_url: dict[str, DiscoveredPostingPayload] = field(default_factory=dict)
```

Update all callers that check `if result.payloads_by_url is not None` to `if result.payloads_by_url`.
Check: `site_utils.py`, `career_site_source.py`, `site_models.py` normalization.

### 8.3 Add parentheses to complex condition in _try_escalate_bypass

**File**: `job_ftch/infrastructure/sources/career_site_source.py:66`

```python
# CURRENT (no parens, relies on operator precedence):
if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 401, 429, 503) or isinstance(exc, httpx.TimeoutException):

# FIX (explicit parens):
if (
    (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (403, 401, 429, 503))
    or isinstance(exc, httpx.TimeoutException)
):
```

---

## Group 9 — MYPY: Fix all 150 mypy errors

Current mypy errors fall into these categories:

### 9.1 Missing type-args for generic dict (ashby.py and others)
Replace `dict` with `dict[str, Any]` in all monitor files.

### 9.2 structlog vs logging logger mixin (ashby.py:174)
`log.warning("msg", url=..., total=..., cap=...)` — structlog uses keyword args, but
file uses `logging.getLogger` not `structlog.get_logger`. Either:
- Change to `structlog.get_logger()` (uses keyword args: `log.warning("msg", url=url)`)
- OR use `logging.getLogger()` format: `log.warning("msg: %s", url)` (no keyword args)
Check all monitor files for logger type consistency.

### 9.3 FSourceV2 TypeVar constraint mismatch (greenhouse.py, hh.py, declarative.py)
The `FSourceV2` TypeVar has a bound that doesn't match these factory signatures.
Fix the TypeVar bound in `registry.py` or adjust the factory signatures to match.

### 9.4 curl_cffi Literal type mismatch (curl_bypass.py)
- The `impersonate` parameter must be a Literal type matching curl_cffi's allowed values
- Use `cast()` or a Literal type alias:
  ```python
  from typing import cast, Literal
  BrowserProfile = Literal["chrome120", "chrome124", ...]  # use the full list from curl_cffi
  
  def __init__(self, impersonate: str = "chrome120") -> None:
      self.impersonate = cast(BrowserProfile, impersonate)
  ```
- Add type annotation: `session: AsyncSession[Any]`
- Add return types to all CurlHttpxAdapter methods

### 9.5 Other missing annotations across new files
Run `mypy job_ftch/ --ignore-missing-imports 2>&1 | grep "error:"` to get full list.
Fix each one: add return types, fix missing type args, resolve TYPE_CHECKING imports.

---

## Group 10 — TESTS: Coverage for new code

### 10.1 Fix failing test: tests/test_outputs.py

Run the failing test to get the full traceback:
```bash
uv run pytest tests/test_outputs.py::test_run_pipeline_summary_reports_extracted_review_and_rejected -v
```
Fix the root cause.

### 10.2 Tests for bypass strategies

**New file**: `tests/test_bypass.py` (already untracked, needs review and staging)

Verify the existing `tests/test_bypass.py` covers:
- `NoopBypass.apply_http()` returns same client
- `CurlBypass` — mock `curl_cffi` import; test `apply_http()` returns adapter
- `CurlHttpxAdapter.__aexit__()` calls `await session.close()` (regression test for the leak fix)
- `AdaptiveBypassManager` — test escalation ladder: `escalate()` moves to next tier, returns False at max
- `CloakBrowserBypass.apply_browser_args()` — test with/without executable_path, with/without cloakbrowser

### 10.3 Tests for monitors

**New file**: `tests/test_career_site_generic.py` (already untracked, needs review)
Plus add to existing test files or new file `tests/test_monitors.py`:

- `dom.discover()` with mock HTML — returns set of URLs matching job keywords
- `dom._extract_links_static()` — with url_filter regex, with keyword fallback
- `rss_board` monitor — with defusedxml, test truncation at MAX_JOBS
- `api_sniffer.can_handle()` — returns None without client, returns dict when API hint found
- `sitemap.discover()` — with mock sitemap XML

### 10.4 Tests for scrapers

Add `tests/test_scrapers.py`:
- `json_ld.scrape()` — with HTML containing JSON-LD job schema
- `embedded.scrape()` — with HTML containing __NEXT_DATA__
- `dom.scrape()` — with step config, extracts title and description
- `dom.can_handle()` — returns steps dict for well-formed HTML, None for empty

### 10.5 Tests for CareerSiteSource

Add to `tests/test_career_site_generic.py` or new file:
- `CareerSiteSource` with mock monitor that returns URLs + mock scraper
- Test bypass escalation path: monitor raises 403 → escalate → retry
- Test "auto" monitor detection path
- Test rich payload path (no scraper needed)

### 10.6 Contract tests for new protocols

Update `tests/test_contracts.py` to include:
- `BoardMonitor` protocol satisfaction check for all registered monitors
- `JobScraper` protocol satisfaction check for all registered scrapers
- `BypassStrategy` protocol satisfaction check for all bypass implementations

---

## Group 11 — CLEANUP: Repo hygiene

### 11.1 Update .gitignore

Add entries (see Group 1.2 above).

### 11.2 Verify module boundary CI check passes

Run:
```bash
grep -r "from job_ftch.infrastructure" job_ftch/application/ job_ftch/domain/ job_ftch/nodes/ job_ftch/sinks/
```
Must return empty after fix 2.1.

### 11.3 Reduce CareerSiteSource.fetch() complexity (optional, low priority)

The 220-line fetch() method should be split into:
- `_run_monitor_with_bypass(monitor_name, monitor_config) -> MonitorResult | None`
- `_scrape_urls(urls, monitor_name, monitor_config) -> AsyncIterator[RawItem]`
- `_save_strategy_cache(domain, monitor_name)`

This is a refactoring-only change, zero behavior change. Mark as optional/follow-up.

---

## Execution order

Execute in this strict order (each step must pass before the next):

1. **Stage untracked files** (Group 1.1) — fixes git integrity BLOCKER
2. **Update .gitignore** (Group 1.2)
3. **Fix CurlHttpxAdapter.close()** (Group 3.1) — BLOCKER-level bug, easy fix
4. **Fix api_sniffer race condition** (Group 3.2)
5. **Fix cost ordering** (Group 4.1)
6. **Move detect_monitor_type to infra** (Group 2.2) — changes registry.py
7. **Fix builder.py layer violation** (Group 2.1) — changes application/builder.py
8. **Move site_models to domain/** (Group 6.1) — enables typed protocols
9. **Fix Protocol return types** (Group 6.1 continued)
10. **Initialize bypass_strategy in __init__** (Group 5.1)
11. **Remove dead load_monitors/load_scrapers** (Group 5.2)
12. **Rename register_source_v2** (Group 6.2)
13. **Fix dataclass mutation** (Group 7.1)
14. **Code quality fixes** (Group 8.1, 8.2, 8.3)
15. **Fix all mypy errors** (Group 9.x)
16. **Fix failing pytest** (Group 10.1)
17. **Add/verify tests** (Group 10.2-10.6)
18. **Run full quality gate**: ruff check, mypy, pytest, bandit, boundary check

## Quality gate (must all pass at end)

```bash
uv run ruff check .                                              # 0 errors
uv run ruff format --check .                                     # 0 errors
uv run mypy job_ftch/ --ignore-missing-imports                   # 0 errors
uv run pytest tests/ -q                                          # 0 failures
uv run bandit -r job_ftch scripts/check_module_boundaries.py -ll # 0 HIGH/MEDIUM issues
grep -r "from job_ftch.infrastructure" job_ftch/application/ job_ftch/domain/ job_ftch/nodes/ job_ftch/sinks/
# ^ must return empty output
```

## Files to modify (summary)

### New files to stage (currently untracked):
- `job_ftch/infrastructure/bypass/adaptive.py`
- `job_ftch/infrastructure/bypass/cloak_bypass.py`
- `job_ftch/infrastructure/bypass/curl_bypass.py`
- `job_ftch/infrastructure/sources/monitors/api_sniffer.py`
- `tests/test_bypass.py`
- `tests/test_career_site_generic.py`
- `docs/adr/022-cloakbrowser-advanced-bypass.md`
- `docs/adr/023-adaptive-scraping-escalation.md`

### Files to modify:
- `.gitignore` — add *.jsonl, plans/, JOBSEEK_*.md, config/test_*.yaml
- `job_ftch/application/registry.py` — remove detect_monitor_type, rename register_source_v2
- `job_ftch/application/contracts.py` — fix Protocol return types
- `job_ftch/application/builder.py` — remove infra imports, use TYPE_CHECKING or injection
- `job_ftch/infrastructure/bypass/curl_bypass.py` — await close(), fix types
- `job_ftch/infrastructure/sources/monitors/api_sniffer.py` — fix race condition, fix cost
- `job_ftch/infrastructure/sources/monitors/dom.py` — fix cost value
- `job_ftch/infrastructure/sources/monitors/__init__.py` — remove load_monitors()
- `job_ftch/infrastructure/sources/scrapers/__init__.py` — remove load_scrapers()
- `job_ftch/infrastructure/sources/career_site_source.py` — init bypass_strategy, fix imports
- `job_ftch/infrastructure/sources/site_utils.py` — use dataclasses.replace()
- `job_ftch/infrastructure/sources/site_models.py` — change payloads_by_url default, move to domain
- `job_ftch/domain/` — add site_models.py (moved from infra)
- All monitor files with mypy errors — add type annotations
- `tests/test_contracts.py` — add protocol satisfaction tests

### New files to create:
- `job_ftch/infrastructure/sources/monitor_detector.py` — extracted detect_monitor_type
- `tests/test_monitors.py` — monitor unit tests
- `tests/test_scrapers.py` — scraper unit tests
