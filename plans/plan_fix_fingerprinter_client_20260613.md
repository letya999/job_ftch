# Plan: Fix SiteFingerprinter client timeout kwarg incompatibility

## Problem
`fingerprint(url, client)` in site_fingerprinter.py calls:
    response = await client.get(url, follow_redirects=True, timeout=8.0)

When called from CareerSiteSource, `client` is `_RetryingHttpClient` (internal wrapper),
which does NOT accept `timeout` as a kwarg. This causes:
    fingerprint_failed_falling_back: "_RetryingHttpClient.get() got an unexpected keyword argument 'timeout'"

The fingerprinter silently falls back to ["dom", "api_sniffer"] — intelligence is lost.

## Fix
In `job_ftch/infrastructure/sources/site_fingerprinter.py`, change the fingerprint function
to create its own plain httpx.AsyncClient internally, instead of using the passed-in `client`.

This makes the fingerprinter self-contained and independent of the calling context.

The `client` parameter becomes unused — keep it for API compatibility but ignore it:

CURRENT:
```python
async def fingerprint(url: str, client: httpx.AsyncClient) -> SiteProfile:
    ...
    try:
        response = await client.get(url, follow_redirects=True, timeout=8.0)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
```

REPLACE WITH:
```python
async def fingerprint(url: str, client: httpx.AsyncClient | None = None) -> SiteProfile:
    """
    Classifies a website based on a fast plain-HTTP probe.
    Does NOT use a browser. Creates its own HTTP client to avoid wrapper incompatibilities.
    The client parameter is accepted but ignored — fingerprinter is self-contained.
    """
    log = logger.bind(url=url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(8.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; SiteFingerprinter/1.0)"},
        ) as probe_client:
            response = await probe_client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
```

Everything else in the function stays the same (status code checks, body scanning, etc.).

## Also fix: remove double fingerprint call in career_site_source.py

Currently CareerSiteSource calls fingerprint TWICE:
1. via detect_monitor_type() → fingerprint()
2. via get_ordered_monitors() → fingerprint()

This is 2 extra HTTP requests. Optimize:

In career_site_source.py, REMOVE the detect_monitor_type() call block entirely for auto mode,
since get_ordered_monitors() now returns the full ordered list including detected_monitor logic.

FIND this block in career_site_source.py:
```python
        # 1. Resolve initial monitor via detection if auto
        detected_monitor = None
        if initial_monitor_name == "auto":
            async with client_for_config(self.http, monitor_config) as monitor_http:
                try:
                    detected = await detect_monitor_type(self.spec.url, monitor_http)
                    if detected:
                        detected_monitor = detected[0]
                        # Merge auto-detected config
                        monitor_config = {**detected[1], **monitor_config}
                except Exception:
                    pass
```

REPLACE WITH:
```python
        # 1. (fingerprinting done below in get_ordered_monitors - no separate detect call needed)
        detected_monitor = None
```

And in the monitors_to_try block, simplify:
```python
        monitors_to_try = []
        if initial_monitor_name == "auto":
            from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
            async with client_for_config(self.http, monitor_config) as _fp_client:
                try:
                    monitors_to_try = await get_ordered_monitors(self.spec.url, _fp_client)
                except Exception:
                    monitors_to_try = ["dom", "api_sniffer"]
            for fallback in ["dom", "api_sniffer"]:
                if fallback not in monitors_to_try:
                    monitors_to_try.append(fallback)
        else:
            monitors_to_try = [initial_monitor_name]
```

## Verification
Run smoke test after fix:
```
python -c "
import asyncio, httpx, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(10))
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint, SiteClass

async def test():
    # Test fingerprinter standalone
    async with httpx.AsyncClient() as c:
        p = await fingerprint('https://hh.ru/vacancies/python-developer', c)
        print(f'hh.ru: {p.site_class}')
        assert p.site_class == SiteClass.SSR

    # Test via career_site in auto mode
    from job_ftch.domain.source_spec import SourceSpec
    from pydantic import TypeAdapter
    from job_ftch.application.registry import create_source_from_spec
    spec = TypeAdapter(SourceSpec).validate_python({
        'type': 'career_site',
        'url': 'https://hh.ru/vacancies/python-developer',
        'source_name': 'hh_auto_test',
        'monitor': 'auto',
        'limit': 2
    })
    count = 0
    async for item in create_source_from_spec(spec).fetch():
        count += 1
        if count >= 2: break
    print(f'items via auto: {count}')
    assert count >= 1

asyncio.run(test())
print('ALL TESTS PASSED')
"
```

Verify NO fingerprint_failed_falling_back warning in output.

## Commit
After tests pass:
  git add job_ftch/infrastructure/sources/site_fingerprinter.py \
          job_ftch/infrastructure/sources/career_site_source.py
  git commit -m "fix(fingerprinter): use self-contained httpx client, remove double probe"
