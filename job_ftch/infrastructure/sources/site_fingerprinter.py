import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class SiteClass(StrEnum):
    SSR = "SSR"
    SPA = "SPA"
    API_JSON = "API_JSON"
    RSS = "RSS"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SiteProfile:
    site_class: SiteClass
    recommended_monitors: list[str]  # ordered best-first
    detected_config: dict[str, Any]  # extra monitor_config hints


async def fingerprint(url: str, client: httpx.AsyncClient | None = None) -> SiteProfile:
    """
    Classifies a website based on a fast plain-HTTP probe.
    Does NOT use a browser. Creates its own HTTP client to avoid wrapper incompatibilities.
    The client parameter is accepted but ignored — fingerprinter is self-contained.
    """
    log = logger.bind(url=url)

    try:
        # Phase 0 - plain HTTP GET
        # We create a new client here because the passed-in client might be a wrapper
        # that doesn't support the 'timeout' or 'follow_redirects' kwargs properly.
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(8.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; SiteFingerprinter/1.0)"},
        ) as probe_client:
            response = await probe_client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
        log.warning("fingerprint_connection_failed", error=str(e))
        return SiteProfile(SiteClass.BLOCKED, ["dom", "api_sniffer"], {})

    # B. If status in (401, 403, 429, 503):
    if response.status_code in (401, 403, 429, 503):
        log.info("site_protected_or_blocked", status=response.status_code)
        return SiteProfile(SiteClass.BLOCKED, ["dom", "api_sniffer"], {})

    # C. Check content-type:
    content_type = response.headers.get("content-type", "").lower()
    if any(rss in content_type for rss in ("rss", "atom", "xml")):
        return SiteProfile(SiteClass.RSS, ["rss_board", "dom"], {})
    if "application/json" in content_type:
        return SiteProfile(SiteClass.API_JSON, ["api_sniffer"], {})

    body = response.text
    # Increased snippet to 100k to handle sites with heavy headers (like hh.ru)
    body_snippet = body[:100000]

    # D. Scan body for vacancy signals:
    # Look for common patterns in links that suggest a job list or vacancy details
    # We use a non-capturing group for the keywords to count individual matches properly
    vacancy_link_re = re.compile(
        r'href=["\'][^"\']*/(?:vacanc|job[s/\-_]|position[s/]|career/\d)', re.IGNORECASE
    )
    vacancy_links = vacancy_link_re.findall(body_snippet)

    # E. If len(vacancy_links) >= 3:
    if len(vacancy_links) >= 3:
        log.debug("detected_ssr_via_links", link_count=len(vacancy_links))
        return SiteProfile(SiteClass.SSR, ["dom"], {})

    # F. Check for SPA indicators in body:
    spa_hints = [
        "__NEXT_DATA__",
        "__NUXT__",
        "window.__INITIAL_STATE__",
        "data-reactroot",
        "ng-version",
        "data-vue-meta",
        '<div id="app"',
        '<div id="root"',
    ]
    is_spa = any(hint.lower() in body_snippet.lower() for hint in spa_hints)

    # G. If is_spa:
    if is_spa:
        log.debug("detected_spa_via_hints")
        return SiteProfile(SiteClass.SPA, ["api_sniffer", "dom"], {})

    # H. If body is short and status == 200:
    # Probably SPA with empty shell (e.g. just <div id="root"></div>)
    stripped_body = body.strip()
    if len(stripped_body) < 3000 and response.status_code == 200:
        log.debug("detected_spa_via_short_body", body_len=len(stripped_body))
        return SiteProfile(SiteClass.SPA, ["api_sniffer", "dom"], {})

    # I. Default:
    log.debug("fingerprint_unknown")
    return SiteProfile(SiteClass.UNKNOWN, ["dom", "api_sniffer"], {})
