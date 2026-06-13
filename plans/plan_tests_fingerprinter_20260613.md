# Plan: Unit tests for SiteFingerprinter and get_ordered_monitors

## Goal
Write tests for the new code added in ADR-025:
- `job_ftch/infrastructure/sources/site_fingerprinter.py` (fingerprint function, SiteClass)
- `job_ftch/infrastructure/sources/monitor_detector.py` (get_ordered_monitors)

## File to CREATE: tests/test_site_fingerprinter.py

Use `pytest` + `pytest-asyncio` + `respx` (or `unittest.mock`/`pytest-httpx`) for HTTP mocking.
Check available: `python -c "import respx; print('respx ok')"` and
`python -c "import pytest_httpx; print('pytest_httpx ok')"`.
If neither available, use `unittest.mock.patch` with `httpx.AsyncClient`.

### Test cases to implement:

```
IMPORTANT: All tests mock HTTP — NO real network calls.
Use respx or pytest-httpx or monkeypatch to mock httpx.AsyncClient responses.
```

#### 1. test_fingerprint_ssr_via_vacancy_links
Mock response: status=200, content-type=text/html,
body contains 5+ href="/vacancy/12345", href="/jobs/engineer" links.
Expected: SiteClass.SSR, recommended_monitors[0] == "dom"

#### 2. test_fingerprint_blocked_403
Mock response: status=403
Expected: SiteClass.BLOCKED, "dom" in recommended_monitors

#### 3. test_fingerprint_blocked_429
Mock response: status=429
Expected: SiteClass.BLOCKED, "dom" in recommended_monitors

#### 4. test_fingerprint_rss_content_type
Mock response: status=200, content-type="application/rss+xml",
body="<rss><channel>...</channel></rss>"
Expected: SiteClass.RSS, recommended_monitors[0] == "rss_board"

#### 5. test_fingerprint_json_api
Mock response: status=200, content-type="application/json",
body='[{"id":1,"title":"Python Dev"}]'
Expected: SiteClass.API_JSON, recommended_monitors[0] == "api_sniffer"

#### 6. test_fingerprint_spa_next_data
Mock response: status=200, content-type=text/html,
body='<html><head></head><body><div id="__next"><script id="__NEXT_DATA__" type="application/json">{"props":{}}</script></div></body></html>'
Expected: SiteClass.SPA, recommended_monitors[0] == "api_sniffer"

#### 7. test_fingerprint_spa_short_body
Mock response: status=200, content-type=text/html,
body='<html><body><div id="root"></div></body></html>' (< 3000 chars, no vacancy links)
Expected: SiteClass.SPA (either via spa_hints or short body detection)

#### 8. test_fingerprint_connection_error
Mock: raise httpx.ConnectError when GET is called
Expected: SiteClass.BLOCKED, recommended_monitors == ["dom", "api_sniffer"]

#### 9. test_fingerprint_timeout
Mock: raise httpx.TimeoutException when GET is called
Expected: SiteClass.BLOCKED

#### 10. test_fingerprint_unknown_sparse_html
Mock response: status=200, content-type=text/html,
body is 10000+ chars of plain text with no vacancy links, no SPA hints
Expected: SiteClass.UNKNOWN, "dom" in recommended_monitors

#### 11. test_get_ordered_monitors_ssr
Uses get_ordered_monitors() from monitor_detector.
Mock response for the URL: 200 + vacancy links in HTML.
Expected: return value starts with "dom"

#### 12. test_get_ordered_monitors_fallback_on_error
Patch fingerprint to raise Exception.
Expected: returns ["dom", "api_sniffer"] (graceful fallback)

#### 13. test_site_profile_frozen_dataclass
SiteProfile is a frozen dataclass — verify immutability:
p = SiteProfile(site_class=SiteClass.SSR, recommended_monitors=["dom"], detected_config={})
Try setting p.site_class = SiteClass.SPA → should raise FrozenInstanceError

### How to mock httpx in fingerprint():

The fingerprint() function creates its OWN httpx.AsyncClient internally:
```python
async with httpx.AsyncClient(...) as probe_client:
    response = await probe_client.get(url)
```

To mock this, use `respx` library if available:
```python
import respx
import httpx

@respx.mock
async def test_fingerprint_ssr_via_vacancy_links():
    respx.get("https://example.com/jobs").mock(
        return_value=httpx.Response(200,
            headers={"content-type": "text/html"},
            text='<a href="/vacancy/123">Job</a>' * 5
        )
    )
    from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint, SiteClass
    profile = await fingerprint("https://example.com/jobs")
    assert profile.site_class == SiteClass.SSR
```

If respx not available, use `unittest.mock.patch("httpx.AsyncClient")`:
```python
from unittest.mock import AsyncMock, patch, MagicMock

async def test_fingerprint_ssr():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = '<a href="/vacancy/123">Job</a>' * 5
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    
    with patch("job_ftch.infrastructure.sources.site_fingerprinter.httpx.AsyncClient",
               return_value=mock_client):
        from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint, SiteClass
        profile = await fingerprint("https://example.com/jobs")
    assert profile.site_class == SiteClass.SSR
```

## After writing tests

Run to verify:
  python -m pytest tests/test_site_fingerprinter.py -v

All 13 tests should pass. Fix any issues found.

Then commit:
  git add tests/test_site_fingerprinter.py
  git commit -m "test(fingerprinter): add unit tests for SiteFingerprinter and monitor ordering"

## Constraints
- No real network calls in tests (mock everything)
- Tests must be fast (<5s total for the batch)
- Use pytest-asyncio (already installed, asyncio_mode=auto in pyproject.toml)
- Do not import from domain/ in test file (use infrastructure imports only)
