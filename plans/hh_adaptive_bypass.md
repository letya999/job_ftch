# Plan: Fix hh.ru / hh.kz via adaptive bypass strategy

## Problem
hh_ru and hh_kz are configured as `type: rest_api` which uses `OfficialAPISource` — a plain httpx
client with NO AdaptiveBypassManager. The hh.ru API returns 403 to direct requests.

## Solution
Switch both to `type: career_site` with `monitor: api_sniffer`.

CareerSiteSource has the full adaptive bypass chain:
  TIERS = ["noop", "curl_stealth", "stealth_browser", "cloak"]
  _try_escalate_bypass() triggers on HTTP 403/401/429/503 or TimeoutException

api_sniffer monitor opens a real Playwright browser on hh.ru search page, captures
XHR calls to api.hh.ru/vacancies (which the hh.ru frontend makes internally).
The browser passes through bot detection; raw HTTP client does not.

## Step 1: Edit config/sources_e2e_20260613.yaml

Find the two entries (lines ~138-157) that look like:
```yaml
  - type: rest_api
    base_url: https://api.hh.ru/
    jobs_endpoint: vacancies
    params:
      text: "python developer"
      area: "1"
      per_page: "5"
    source_name: hh_ru

  - type: rest_api
    base_url: https://api.hh.ru/
    jobs_endpoint: vacancies
    params:
      text: "python developer"
      area: "159"
      per_page: "5"
    source_name: hh_kz
```

Replace them EXACTLY with:
```yaml
  - type: career_site
    url: https://hh.ru/search/vacancy?text=python+developer&area=1
    source_name: hh_ru
    monitor: api_sniffer
    monitor_config:
      api_url_match: "api\.hh\.(ru|kz)/vacancies"
      settle_seconds: 6
    limit: 5

  - type: career_site
    url: https://hh.kz/search/vacancy?text=python+developer&area=160
    source_name: hh_kz
    monitor: api_sniffer
    monitor_config:
      api_url_match: "api\.hh\.(ru|kz)/vacancies"
      settle_seconds: 6
    limit: 5
```

## Step 2: Verify the edit
Run: grep -A 8 "source_name: hh_ru\|source_name: hh_kz" config/sources_e2e_20260613.yaml
Expected: shows `type: career_site` and `monitor: api_sniffer` for both.

## Step 3: Run targeted probe on just hh_ru
Run: python scripts/e2e_probe.py --sources hh_ru 2>/dev/null

If the probe script doesn't support --sources flag, use:
python -c "
import asyncio
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))

from job_ftch.domain.source_spec import SourceSpec
from pydantic import TypeAdapter
from job_ftch.application.registry import create_source_from_spec

spec_data = {
    'type': 'career_site',
    'url': 'https://hh.ru/search/vacancy?text=python+developer&area=1',
    'source_name': 'hh_ru',
    'monitor': 'api_sniffer',
    'monitor_config': {'api_url_match': 'api\\.hh\\.(ru|kz)/vacancies', 'settle_seconds': 6},
    'limit': 3
}

async def main():
    ta = TypeAdapter(SourceSpec)
    spec = ta.validate_python(spec_data)
    src = create_source_from_spec(spec)
    count = 0
    async for item in src.fetch():
        count += 1
        print(f'  item {count}: {item.external_id or item.url}')
        if count >= 3:
            break
    print(f'RESULT: {count} items')

asyncio.run(main())
"

## Step 4: If still 0 items, try with explicit bypass escalation
If api_sniffer returns 0 items (403 on browser navigation), add bypass field to spec:
```yaml
    bypass: stealth_browser
```
or
```yaml
    bypass: cloak
```

## Success criteria
hh_ru or hh_kz returns >= 1 item with non-empty external_id or url.
Report the actual items found and what tier of bypass was used.
