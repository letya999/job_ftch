import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Real Chrome UA + Accept for the fingerprint probe. The previous
# "Mozilla/5.0 (compatible; SiteFingerprinter/1.0)" UA is instantly
# recognized and blocked by anti-bot vendors (Cloudflare/DataDome),
# which poisons monitor selection before the real fetch ever happens.
# Defined locally (not imported from career_site.py/browser_utils.py)
# to avoid a cross-file import cycle between those modules and this one.
PROBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
PROBE_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_KNOWN_BOARD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"jobs\.ashbyhq\.com/[\w-]+", re.IGNORECASE), "ashby"),
    (
        re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?[\w-]+", re.IGNORECASE),
        "greenhouse",
    ),
    (re.compile(r"job-boards(?:\.[\w-]+)?\.greenhouse\.io/[\w-]+", re.IGNORECASE), "greenhouse"),
    (re.compile(r"jobs\.(?:eu\.)?lever\.co/[\w-]+", re.IGNORECASE), "lever_board"),
    (re.compile(r"[\w-]+\.wd\d+\.myworkdayjobs\.com/", re.IGNORECASE), "workday"),
    (re.compile(r"apply\.workable\.com/[\w-]+", re.IGNORECASE), "workable"),
    (re.compile(r"jobs\.deel\.com/(?:job-boards/)?[\w-]+", re.IGNORECASE), "deel"),
    (re.compile(r"[\w-]+\.breezy\.hr/?$", re.IGNORECASE), "breezy"),
    (re.compile(r"[\w-]+\.jobs\.personio\.\w+", re.IGNORECASE), "personio"),
    (re.compile(r"[\w-]+\.recruitee\.com/?$", re.IGNORECASE), "recruitee"),
    (
        re.compile(
            r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?[\w-]+/jobs", re.IGNORECASE
        ),
        "rippling",
    ),
    (re.compile(r"jobs\.smartrecruiters\.com/[\w-]+", re.IGNORECASE), "smartrecruiters"),
    (re.compile(r"careers\.smartrecruiters\.com/[\w-]+", re.IGNORECASE), "smartrecruiters"),
    (re.compile(r"join\.com/companies/[\w-]+", re.IGNORECASE), "join"),
    (re.compile(r"[\w-]+\.softgarden\.io", re.IGNORECASE), "softgarden"),
)


def _specific_monitor_from_text(url: str, body: str | None) -> str | None:
    haystacks = [url]
    parsed = urlparse(url)
    host = parsed.netloc
    if host:
        haystacks.append(host)
    if body:
        from job_ftch.config import get_settings

        haystacks.append(body[: get_settings().fingerprint_body_scan_max_chars])
    for pattern, monitor_name in _KNOWN_BOARD_PATTERNS:
        if any(pattern.search(text) for text in haystacks):
            return monitor_name
    return None


async def _probe_with_client(url: str, client: httpx.AsyncClient) -> httpx.Response:
    try:
        return await client.get(
            url,
            follow_redirects=True,
            headers={"User-Agent": PROBE_USER_AGENT, "Accept": PROBE_ACCEPT},
        )
    except TypeError:
        return await client.get(url, follow_redirects=True)


@asynccontextmanager
async def _probe_client_with_stealth_fallback(client: Any) -> AsyncIterator[Any]:
    active_client = client
    owns_client = False
    try:
        from job_ftch.application.registry import resolve_bypass

        active_client = await resolve_bypass("curl_stealth").apply_http(client)
        owns_client = active_client is not client
    except (ImportError, ValueError, KeyError):
        active_client = client
        owns_client = False

    if owns_client and hasattr(active_client, "__aenter__") and hasattr(active_client, "__aexit__"):
        async with active_client as managed_client:
            yield managed_client
        return

    yield active_client


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
    canonical_url: str | None = None  # post-redirect URL when different from input


def known_monitor_from_url(url: str) -> str | None:
    return _specific_monitor_from_text(url, None)


def _has_jsonld_job_posting(body: str) -> bool:
    lower = body.lower()
    return "application/ld+json" in lower and "jobposting" in lower


async def fingerprint(url: str, client: httpx.AsyncClient | None = None) -> SiteProfile:
    """
    Classifies a website based on a fast plain-HTTP probe.
    Does NOT use a browser. When a prepared client is available, reuse it so
    any already-selected bypass / proxy policy applies to the probe as well.
    """
    log = logger.bind(url=url)

    known_monitor = known_monitor_from_url(url)
    if known_monitor is not None:
        log.debug("detected_known_board_from_url", monitor=known_monitor)
        return SiteProfile(SiteClass.SSR, [known_monitor], {})

    try:
        if client is not None:
            # A caller-supplied client already represents the selected runtime
            # route (transport, proxy and session). Rewrapping it here would make
            # the probe observe a different route from the subsequent monitor.
            response = await _probe_with_client(url, client)
        else:
            from job_ftch.config import get_settings

            async with (
                httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=httpx.Timeout(get_settings().fingerprinter_timeout_seconds),
                    headers={"User-Agent": PROBE_USER_AGENT, "Accept": PROBE_ACCEPT},
                ) as probe_client,
                _probe_client_with_stealth_fallback(probe_client) as active_client,
            ):
                response = await _probe_with_client(url, active_client)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as e:
        log.warning("fingerprint_connection_failed", error=str(e))
        return SiteProfile(SiteClass.BLOCKED, ["dom", "api_sniffer"], {})
    except Exception as e:
        if "curl_cffi" in str(type(e)):
            log.warning("fingerprint_cffi_failed", error=str(e))
            return SiteProfile(SiteClass.BLOCKED, ["dom", "api_sniffer"], {})
        raise

    body = response.text
    final_url = str(response.url)
    _canonical = final_url if final_url.rstrip("/") != url.rstrip("/") else None

    from job_ftch.infrastructure.sources.monitors.shared import is_board_gone

    if is_board_gone(body):
        log.info("site_board_gone_detected")
        return SiteProfile(SiteClass.SSR, [], {"board_gone": True}, canonical_url=_canonical)

    from job_ftch.infrastructure.bypass.failure_signal import HeuristicFailureSignal

    challenge = (
        HeuristicFailureSignal()
        .classify_detailed(
            status_code=response.status_code,
            body=body.encode("utf-8", errors="ignore"),
            error=None,
        )
        .challenge
    )
    if challenge:
        log.info("site_challenge_detected", status=response.status_code)
        return SiteProfile(
            SiteClass.BLOCKED,
            ["dom", "api_sniffer"],
            {"render": True, "challenge": True},
            canonical_url=_canonical,
        )

    # B. If status in (401, 403, 429, 503):
    if response.status_code in (401, 403, 429, 503):
        log.info("site_protected_or_blocked", status=response.status_code)
        return SiteProfile(SiteClass.BLOCKED, ["dom", "api_sniffer"], {}, canonical_url=_canonical)

    # C. Check content-type:
    content_type = response.headers.get("content-type", "").lower()
    if any(rss in content_type for rss in ("rss", "atom", "xml")):
        return SiteProfile(SiteClass.RSS, ["rss_board", "dom"], {}, canonical_url=_canonical)
    if "application/json" in content_type:
        return SiteProfile(SiteClass.API_JSON, ["api_sniffer"], {}, canonical_url=_canonical)

    specific_monitor = _specific_monitor_from_text(str(response.url), body)
    if specific_monitor is not None:
        log.debug("detected_known_board", monitor=specific_monitor)
        return SiteProfile(SiteClass.SSR, [specific_monitor], {}, canonical_url=_canonical)

    has_jsonld = _has_jsonld_job_posting(body)
    if has_jsonld:
        log.debug("detected_jsonld_job_posting")
        return SiteProfile(SiteClass.SSR, ["dom"], {"has_jsonld": True}, canonical_url=_canonical)

    body_snippet = body

    # D. Scan body for vacancy signals:
    vacancy_link_re = re.compile(
        r'href=["\'][^"\']*/(?:vacanc|job[s/\-_]|position[s/]|career/\d|offers/\d)', re.IGNORECASE
    )
    vacancy_links = vacancy_link_re.findall(body_snippet)

    # E. If len(vacancy_links) >= 3:
    if len(vacancy_links) >= 3:
        log.debug("detected_ssr_via_links", link_count=len(vacancy_links))
        return SiteProfile(SiteClass.SSR, ["dom"], {}, canonical_url=_canonical)

    # F. Check for SPA indicators in body:
    spa_hints = [
        "__NEXT_DATA__",
        "__next_f",
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
        return SiteProfile(
            SiteClass.SPA, ["api_sniffer", "dom"], {"render": True}, canonical_url=_canonical
        )

    # H. If body is short and status == 200:
    stripped_body = body.strip()
    if len(stripped_body) < 3000 and response.status_code == 200:
        log.debug("detected_spa_via_short_body", body_len=len(stripped_body))
        return SiteProfile(
            SiteClass.SPA, ["api_sniffer", "dom"], {"render": True}, canonical_url=_canonical
        )

    # I. Default:
    log.debug("fingerprint_unknown")
    return SiteProfile(
        SiteClass.UNKNOWN, ["sitemap", "api_sniffer", "dom"], {}, canonical_url=_canonical
    )
