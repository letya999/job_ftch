"""Softgarden ATS monitor.

Classic boards ({slug}.softgarden.io) embed all job IDs in inline JavaScript.
No API credentials needed.

Returns detail URLs only — the json-ld scraper extracts JobPosting data.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_monitor
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_JOBS = 10_000

_IGNORE_SLUGS = frozenset({"www", "api", "app", "static", "cdn"})

_JOB_IDS_RE = re.compile(r"var\s+complete_job_id_list\s*=\s*(?:jobs_selected\s*=\s*)?\[([^\]]*)\]")

_PAGE_MARKERS = [
    re.compile(r"softgarden\.io/assets/"),
    re.compile(r"tracker\.softgarden\.de"),
    re.compile(r"matomo\.softgarden\.io"),
    re.compile(r"certificate\.softgarden\.io"),
    re.compile(r"powered by softgarden", re.IGNORECASE),
    re.compile(r"mediaassets\.softgarden\.de"),
]

# ── URL helpers ──────────────────────────────────────────────────────────


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a *.softgarden.io URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith(".softgarden.io"):
        slug = host.removesuffix(".softgarden.io")
        if slug and slug not in _IGNORE_SLUGS:
            return slug
    return None


def _board_url(slug: str) -> str:
    return f"https://{slug}.softgarden.io"


def _job_url(base: str, job_id: int | str, pattern: str = "{base}/job/{id}?l=en") -> str:
    return pattern.replace("{base}", base).replace("{id}", str(job_id))


# ── Listing page parsing ────────────────────────────────────────────────


def _extract_job_ids(html: str) -> list[int]:
    """Extract job IDs from the listing page's inline JavaScript."""
    match = _JOB_IDS_RE.search(html)
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    ids: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if token:
            try:
                ids.append(int(token))
            except ValueError:
                continue
    return ids


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    """Discover job URLs from a Softgarden board."""
    board_url = spec.url
    config = spec.monitor_config or {}
    slug = config.get("slug") or _slug_from_url(board_url)

    if not slug:
        log.warning("softgarden.missing_slug", url=board_url)
        return set()

    base = _board_url(slug)
    pattern = config.get("job_url_pattern", "{base}/job/{id}?l=en")

    # Fetch listing page
    resp = await client.get(base, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Extract job IDs
    job_ids = _extract_job_ids(html)
    if not job_ids:
        log.info("softgarden.no_jobs", slug=slug)
        return set()

    log.info("softgarden.listed", slug=slug, jobs=len(job_ids))

    urls = {_job_url(base, jid, pattern) for jid in job_ids}

    if len(urls) > MAX_JOBS:
        log.warning("softgarden.truncated", slug=slug, total=len(urls), cap=MAX_JOBS)
        urls = set(sorted(urls)[:MAX_JOBS])

    return urls


# ── Probing ──────────────────────────────────────────────────────────────


async def _probe_listing(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe a Softgarden listing page. Returns (found, job_count)."""
    try:
        resp = await client.get(_board_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        job_ids = _extract_job_ids(resp.text)
        if job_ids:
            return True, len(job_ids)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Detect Softgarden: URL domain match -> page HTML markers scan."""
    # 1. Direct *.softgarden.io URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_listing(slug, client)
            if found:
                result: dict[str, Any] = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
            return {"slug": slug}
        return {"slug": slug}

    if client is None:
        return None

    # 2. HTML scan for Softgarden markers
    html = await fetch_page_text(url, client)
    if html:
        for marker in _PAGE_MARKERS:
            if marker.search(html):
                # Try to extract a softgarden.io slug from the HTML
                for slug_match in re.finditer(r"([\w-]+)\.softgarden\.io", html):
                    found_slug = slug_match.group(1)
                    if found_slug in _IGNORE_SLUGS:
                        continue
                    log.info("softgarden.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_listing(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result
                break  # Marker found

    return None


register_monitor(
    "softgarden",
    discover,
    cost=70,
    rich=False,
    can_handle=can_handle,
    assessment_hint=known_board_assessment_hint(
        "monitor_shape",
        "softgarden",
        url_patterns=(r"[\w-]+\.softgarden\.io",),
        has_stable_id=True,
    ),
)
