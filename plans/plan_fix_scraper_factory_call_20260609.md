# Fix: Scraper factory calling convention in CareerSiteSource

## Problem

In `job_ftch/infrastructure/sources/career_site_source.py`, method `_scrape_with_fallback`,
the scraper is called incorrectly:

```python
scraper_impl = scraper_entry.factory(self.spec.scraper_config, self.http)
if hasattr(scraper_impl, "scrape"):
    payload = await scraper_impl.scrape(url, self.spec.scraper_config, self.http)
else:
    payload = await scraper_impl(url, self.spec.scraper_config, self.http)
```

But scrapers are registered as:
```python
register_scraper("json-ld", scrape, ...)
```

where `scrape` is `async def scrape(url: str, config: dict, http: httpx.AsyncClient)`.

So `scraper_entry.factory` IS the scrape function directly.
Calling `factory(config, http)` maps to `scrape(config_as_url, http_as_config)` — completely wrong.
This causes: `scrape() missing 1 required positional argument: 'http'`

## Fix

### File to MODIFY: job_ftch/infrastructure/sources/career_site_source.py

Read the file first. Find the `_scrape_with_fallback` method.

Replace the entire block inside the try:
```python
scraper_entry = resolve_scraper(scraper_name)
# Factory might return a coroutine (scrape) or an object with scrape()
scraper_impl = scraper_entry.factory(self.spec.scraper_config, self.http)

if hasattr(scraper_impl, "scrape"):
    payload = await scraper_impl.scrape(url, self.spec.scraper_config, self.http)
else:
    payload = await scraper_impl(url, self.spec.scraper_config, self.http)
```

With:
```python
scraper_entry = resolve_scraper(scraper_name)
payload = await scraper_entry.factory(url, self.spec.scraper_config, self.http)
```

The factory field in ScraperEntry IS the scrape function itself — call it directly with
(url, config, http) as the three required positional arguments.

## Verification

After the fix, run:
```
python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```
Expected: 234 passed, 10 skipped.

Then run:
```
python -c "
import asyncio, httpx
from job_ftch.application.registry import load_extensions, resolve_scraper
load_extensions()
scraper = resolve_scraper('json-ld')
print('scraper factory:', scraper.factory)
print('OK - factory is callable with (url, config, http)')
"
```

## Instructions

1. Read career_site_source.py fully before editing.
2. Make ONLY the described change — do not refactor anything else.
3. The fix is replacing 5 lines with 1 line in `_scrape_with_fallback`.
