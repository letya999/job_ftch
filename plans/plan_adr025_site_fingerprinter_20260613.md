# Plan: ADR-025 + SiteFingerprinter Implementation

## Goal
Replace the current blind monitor escalation chain with a fast site classification
(fingerprinting) step that determines optimal monitor order before any attempt.

## Files to create / modify

### 1. CREATE: docs/adr/025-adaptive-site-intelligence.md
Write a proper ADR document with these sections:
- Status: Accepted
- Context: current problem (blind escalation, manual hardcoding, wrong monitors tried first)
- Decision: SiteFingerprinter classifies sites via plain-HTTP probe before monitor selection
- Site classes and their optimal monitor order (table)
- How it integrates with existing bypass escalation
- Consequences (positive + trade-offs)

### 2. CREATE: job_ftch/infrastructure/sources/site_fingerprinter.py
New module. No imports outside stdlib + httpx + structlog (no domain imports).

```
SiteClass enum values: SSR, SPA, API_JSON, RSS, BLOCKED, UNKNOWN

SiteProfile dataclass:
  - site_class: SiteClass
  - recommended_monitors: list[str]  # ordered best-first
  - detected_config: dict[str, Any]  # extra monitor_config hints

async def fingerprint(url: str, client: httpx.AsyncClient) -> SiteProfile:
    Phase 0 - plain HTTP GET (timeout=8s, follow_redirects=True):

    A. If exception (connection error, timeout):
       return SiteProfile(BLOCKED, ["dom", "api_sniffer"], {})

    B. If status in (401, 403, 429, 503):
       return SiteProfile(BLOCKED, ["dom", "api_sniffer"], {})
       # bypass escalation will handle the rest in CareerSiteSource

    C. Check content-type:
       - "rss", "atom", "xml" → RSS, ["rss_board", "dom"], {}
       - "application/json" → API_JSON, ["api_sniffer"], {}

    D. Scan body (resp.text, first 50k chars) for vacancy signals:
       vacancy_link_re = re.compile(
           r'href=["\'][^"\']*/(vacanc|job[s/\-_]|position[s/]|career/\d)',
           re.IGNORECASE
       )
       vacancy_links = vacancy_link_re.findall(body[:50000])

    E. If len(vacancy_links) >= 3:
       return SiteProfile(SSR, ["dom"], {})
       # SSR: data already in plain HTML, dom monitor works without browser

    F. Check for SPA indicators in body:
       spa_hints = [
           "__NEXT_DATA__", "__NUXT__", "window.__INITIAL_STATE__",
           "data-reactroot", "ng-version", "data-vue-meta",
           '<div id="app"', '<div id="root"',
       ]
       is_spa = any(hint.lower() in body.lower() for hint in spa_hints)

    G. If is_spa:
       return SiteProfile(SPA, ["api_sniffer", "dom"], {})

    H. If body is short (< 3000 chars stripped) and status == 200:
       # Probably SPA with empty shell
       return SiteProfile(SPA, ["api_sniffer", "dom"], {})

    I. Default:
       return SiteProfile(UNKNOWN, ["dom", "api_sniffer"], {})
```

### 3. MODIFY: job_ftch/infrastructure/sources/monitor_detector.py
Replace current logic entirely.

New function signature stays the same:
  async def detect_monitor_type(url, client) -> tuple[str, dict] | None

New implementation:
  1. Call fingerprint(url, client) from site_fingerprinter
  2. Return (recommended_monitors[0], detected_config) if recommended_monitors else None
  3. Keep fallback: if fingerprint raises, fall back to old can_handle() iteration

Also add:
  async def get_ordered_monitors(url, client) -> list[str]:
      """Returns full ordered monitor list based on site fingerprint."""
      profile = await fingerprint(url, client)
      return profile.recommended_monitors

### 4. MODIFY: job_ftch/infrastructure/sources/career_site_source.py
In the monitors_to_try construction (lines ~140-148 of current code):

CURRENT CODE (do not change lines outside this block):
```python
monitors_to_try = []
if initial_monitor_name == "auto":
    if detected_monitor and detected_monitor not in ("dom", "api_sniffer"):
        monitors_to_try.append(detected_monitor)
    monitors_to_try.extend(["dom", "api_sniffer"])
else:
    monitors_to_try = [initial_monitor_name]
```

REPLACE WITH:
```python
monitors_to_try = []
if initial_monitor_name == "auto":
    # Use fingerprinter-ordered list instead of fixed fallback
    from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors
    async with client_for_config(self.http, monitor_config) as _fp_client:
        try:
            fp_monitors = await get_ordered_monitors(self.spec.url, _fp_client)
        except Exception:
            fp_monitors = ["dom", "api_sniffer"]
    # Prepend any non-dom/api_sniffer detected monitor (e.g. rss_board from can_handle)
    if detected_monitor and detected_monitor not in fp_monitors:
        monitors_to_try = [detected_monitor] + fp_monitors
    else:
        monitors_to_try = fp_monitors
    # Ensure dom and api_sniffer always present as ultimate fallbacks
    for fallback in ["dom", "api_sniffer"]:
        if fallback not in monitors_to_try:
            monitors_to_try.append(fallback)
else:
    monitors_to_try = [initial_monitor_name]
```

IMPORTANT: The import inside the function is intentional (avoids circular imports).
Do NOT add top-level import.

## After implementation

Run this quick smoke test to verify:
```
python -c "
import asyncio, httpx
from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint, SiteClass

async def test():
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
        p = await fingerprint('https://hh.ru/vacancies/python-developer', c)
        print(f'hh.ru: {p.site_class} -> {p.recommended_monitors}')
        assert p.site_class == SiteClass.SSR, f'Expected SSR, got {p.site_class}'
        assert p.recommended_monitors[0] == 'dom', f'Expected dom first'

        p2 = await fingerprint('https://yandex.ru/jobs', c)
        print(f'yandex: {p2.site_class} -> {p2.recommended_monitors}')
        assert p2.site_class == SiteClass.BLOCKED, f'Expected BLOCKED for yandex'

asyncio.run(test())
print('OK')
"
```

If hh.ru returns SSR and yandex returns BLOCKED, implementation is correct.
Also verify no import errors:
  python -c "from job_ftch.infrastructure.sources.site_fingerprinter import fingerprint; print('import OK')"
  python -c "from job_ftch.infrastructure.sources.monitor_detector import get_ordered_monitors; print('import OK')"

## Commit
After all tests pass:
  git add docs/adr/025-adaptive-site-intelligence.md \
          job_ftch/infrastructure/sources/site_fingerprinter.py \
          job_ftch/infrastructure/sources/monitor_detector.py \
          job_ftch/infrastructure/sources/career_site_source.py
  git commit -m "feat(sources): add site fingerprinting for adaptive monitor selection (ADR-025)"

## Constraints (from AGENTS.md)
- No domain/ imports in site_fingerprinter.py
- No if/elif dispatch by adapter kind in core (the fingerprinter is data-driven, not conditional on monitor names in core)
- SiteFingerprinter must not require browser — it is a plain-HTTP probe only
- Keep backward compatibility: explicit monitor: dom / monitor: api_sniffer still works unchanged
