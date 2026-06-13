# Plan: Fix test failures and mypy errors after monitor/scraper implementation

## Context

The monitor/scraper implementation (plan_career_site_monitor_scraper_20260609.md) completed
with 233/235 tests passing and mypy errors. Two fixes needed:

## Fix 1 — isinstance conflict in test_phase11_multisource.py

### Problem
`tests/test_phase11_multisource.py:146` does:
```python
assert isinstance(source, CareerSiteSource)
```
But the test imports `CareerSiteSource` from one path while the registry creates it from another,
causing the isinstance check to fail on different class objects.

### Investigation needed
1. Read `tests/test_phase11_multisource.py` around line 146 to see what is imported.
2. Read `job_ftch/infrastructure/sources/__init__.py` to see what is exported.
3. Read `job_ftch/application/registry.py` to see what class is used to create career_site source.
4. Read `job_ftch/infrastructure/sources/career_site_source.py` to understand the class.

### Fix
Ensure there is ONE canonical `CareerSiteSource` class used everywhere:
- Either `job_ftch.infrastructure.sources.career_site_source.CareerSiteSource`
- Or the class re-exported from `job_ftch.infrastructure.sources.__init__`

The test's import and the registry factory must point to the exact same class object.
If there are two CareerSiteSource classes (old declarative + new), they must have
different names or the old one must be removed/renamed.

## Fix 2 — mypy errors in career_site_source.py

### File: `job_ftch/infrastructure/sources/career_site_source.py` line ~191

Error: `Unexpected keyword argument "url" for "warning" of "Logger"`

This means structlog-style `logger.warning("msg", key=val)` was used with stdlib `logging.Logger`.
Fix: use `logger.warning("msg %s %s", url, chain)` (stdlib format) OR switch to structlog.

Check what logger type is used in this file:
- If `import logging; logger = logging.getLogger(...)` → use `logger.warning("text %s", val)` format
- If `import structlog; log = structlog.get_logger()` → `log.warning("text", key=val)` is correct

Error: `Argument "http_client" to "CareerSiteSource" has incompatible type "_RetryingHttpClient"; expected "AsyncClient"`

Fix: The type annotation for `http_client` parameter in `CareerSiteSource.__init__` should accept
the actual type passed. Either:
- Widen annotation to `httpx.AsyncClient | Any` with TYPE_CHECKING guard
- Or use `httpx.AsyncClient` as the annotation (the actual arg IS an AsyncClient subtype)
- Check what `_RetryingHttpClient` is — if it inherits from `httpx.AsyncClient`, the annotation is wrong

## Fix 3 — mypy errors in scrapers/nextdata.py

### File: `job_ftch/infrastructure/sources/scrapers/nextdata.py`

Errors: `Missing type arguments for generic type "dict"`

Fix: Replace bare `dict` annotations with `dict[str, Any]` throughout the file.
Add `from typing import Any` at the top if not present.

All occurrences of bare `dict` as a type annotation should become `dict[str, Any]`.

## Fix 4 — feedparser test (pre-existing, not introduced by this PR)

### File: `tests/e2e/test_level2_pipeline.py::test_pipeline_rss_to_json_sink`

The test fails because `feedparser` is not installed. This is a pre-existing issue.
Fix: Add `pytest.importorskip("feedparser")` at the top of the test function, OR
install feedparser: run `uv add feedparser --optional feeds` in the project.

Check `pyproject.toml` — if feedparser is already in `[feeds]` optional group, the test
should be marked with `@pytest.mark.skipif(not feedparser_available, reason="feedparser not installed")`.

Look at how other optional-dep tests are skipped in the test suite (e.g., postgres tests use `pytest.mark.skip`).

## Verification after fixes

Run:
```
python -m mypy job_ftch/ --ignore-missing-imports 2>&1 | grep "error:" | wc -l
```
Expected: 0 errors (or same count as before this PR, i.e., pre-existing errors only).

Run:
```
python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```
Expected: at most 2 skipped (postgres integration tests), 0 failed.

## Files to read first (required for context)
- `job_ftch/infrastructure/sources/career_site_source.py`
- `job_ftch/infrastructure/sources/__init__.py`
- `job_ftch/infrastructure/sources/scrapers/nextdata.py`
- `tests/test_phase11_multisource.py` (around line 146)
- `tests/e2e/test_level2_pipeline.py` (around line 63)
