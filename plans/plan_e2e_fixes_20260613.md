# Fix Plan: E2E Probe Bugs + Source Timeout Improvements — 2026-06-13

## Problems identified from e2e probe run

### Bug 1: Probe script incorrect type remapping (scripts/e2e_probe.py)
The probe script remaps:
- `rss_feed` → `rss` (WRONG: SourceSpec uses `rss_feed` literal, there is no `rss`)
- `rest_api` with hh.ru URL → `hh_api` (WRONG: HH adapter is registered as `hh_api` factory key
  but still uses `rest_api` as the SourceSpec type literal)

Fix: Remove both remapping blocks entirely. Use spec types as-is from YAML.

### Bug 2: HH.ru YAML pagination type invalid (config/sources_e2e_20260613.yaml)
The YAML has `pagination: type: page` but PaginationConfig only supports 
`cursor`, `offset`, `link_header`. `page` is unknown → ValidationError.

Fix: Remove the pagination block from hh_ru and hh_kz entries in the YAML.
HH API returns the first page (per_page=5) without pagination config needed for probe purposes.

### Bug 3: RemoteOK `external_id` crash (job_ftch/infrastructure/sources/api/base.py)
The RemoteOK API response starts with a metadata dict:
  `{"last_updated": 1781298457, "legal": "API Terms of Service: ..."}` 
This has no `id` field → `external_id=str(item.get("id") or ...)` → `""` → 
RawItem ValidationError (min length 1).

Fix: In `OfficialAPISource.fetch()`, wrap `yield self._map_to_raw_item(item)` in 
a try-except that catches `ValidationError` and logs a debug message, then `continue`.
This is defensive against any malformed/metadata items from any REST API.

Exact location: `job_ftch/infrastructure/sources/api/base.py`, inside the `for item in items:` loop
at line ~83. The current loop is:
```
for item in items:
    yield self._map_to_raw_item(item)
```
Change to:
```
for item in items:
    try:
        yield self._map_to_raw_item(item)
    except Exception as item_exc:
        logger.debug("skipping_unmappable_item", extra={"keys": list(item.keys()), "error": str(item_exc)})
        continue
```

### Fix 4: Increase default career_site timeout (config/sources_e2e_20260613.yaml)
18 sources timed out at 15s. Many are legitimately slow (sber, kaspi, sergek, air_astana).
Fix: In the probe script (scripts/e2e_probe.py), increase TIMEOUT_S from 15 to 30.
Do NOT change the global settings — this is a probe-specific adjustment.

### Fix 5: Switch known SPA sites to browser type (config/sources_e2e_20260613.yaml)
These sites serve a blank page over plain HTTP (all JS-rendered):
- yandex_jobs → change `monitor: auto` to `monitor: playwright` and add `scraper: dom`
- ozon_tech → 403 even after escalation, SPA — add `bypass: stealth_browser`
- x5_tech_career → no DOM jobs found, SPA

For these 3, change their spec from:
```yaml
- type: career_site
  url: <url>
  monitor: auto
```
to:
```yaml
- type: career_site
  url: <url>
  monitor: auto
  monitor_config:
    render: true
    wait: domcontentloaded
```

Do NOT add `browser` type (that spec requires playwright installed separately).
Just set `render: true` in monitor_config for the SPA sites.

## Files to modify

1. `scripts/e2e_probe.py`
   - Remove `type_to_parse` logic block (lines ~120-127), use `spec_dict` as-is for TypeAdapter
   - Increase `TIMEOUT_S = 15` → `TIMEOUT_S = 30`

2. `config/sources_e2e_20260613.yaml`
   - hh_ru: remove the `pagination:` block (keep type, base_url, jobs_endpoint, params, source_name)
   - hh_kz: same
   - yandex_jobs: add `monitor_config: {render: true, wait: domcontentloaded}`
   - ozon_tech: add `monitor_config: {render: true, wait: domcontentloaded}`
   - x5_tech_career: add `monitor_config: {render: true, wait: domcontentloaded}`

3. `job_ftch/infrastructure/sources/api/base.py`
   - In the `for item in items:` loop, wrap `yield self._map_to_raw_item(item)` with try-except
   - Catch broad `Exception` (not just ValidationError since _map_to_raw_item can raise other things)
   - Log debug and continue on failure

## After fixes: re-run probe
Run: `python scripts/e2e_probe.py 2>scripts/e2e_probe_stderr.txt`
Capture the summary table and report results. 
Expected improvement: hh_ru, hh_kz, habr_career_ml, weworkremotely → OK; remoteok → OK.
