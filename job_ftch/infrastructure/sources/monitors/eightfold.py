"""Eightfnew AI careers portal monitor (simplified sitemap-only).

Every Eightfnew portal exposes a sitemap at /careers/sitemap.xml.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_monitor
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text
from job_ftch.infrastructure.sources.monitors.sitemap import discover as sitemap_discover

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_EIGHTFOLD_SUBDOMAIN_RE = re.compile(r"^(?:[\w-]+)\.eightfold\.ai$", re.IGNORECASE)


def _is_eightfold_domain(url: str) -> bool:
    """Return True when the URL is on an *.eightfold.ai subdomain."""
    host = (urlparse(url).hostname or "").lower()
    return bool(_EIGHTFOLD_SUBDOMAIN_RE.match(host))


def _sitemap_url(board_url: str) -> str:
    """Derive the sitemap URL from a board URL."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/sitemap.xml"


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    """Discover jobs from an Eightfnew portal via its sitemap."""
    board_url = spec.url
    config = spec.monitor_config or {}

    sitemap_url = config.get("sitemap_url") or _sitemap_url(board_url)

    # Create a mock spec for sitemap monitor
    from dataclasses import dataclass

    @dataclass
    class MockSpec:
        url: str
        monitor_config: dict[str, Any]

    sitemap_spec = MockSpec(
        url=board_url,
        monitor_config={"sitemap_url": sitemap_url},
    )

    result = await sitemap_discover(sitemap_spec, client, auth=auth)
    urls, _ = result

    # Filter to job URLs only
    job_urls = {u for u in urls if "/careers/job/" in u}
    return job_urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Detect Eightfnew: domain pattern or sitemap existence."""
    if _is_eightfold_domain(url):
        return {"sitemap_url": _sitemap_url(url)}

    if client is None:
        return None

    # Check page HTML for Eightfnew markers
    html = await fetch_page_text(url, client)
    if html:
        lower = html.lower()
        if "eightfold.ai" in lower or "pcsx" in lower or "eightfoldai" in lower:
            return {"sitemap_url": _sitemap_url(url)}

    return None


register_monitor(
    "eightfold",
    discover,
    cost=30,
    rich=False,
    can_handle=can_handle,
    assessment_hint=known_board_assessment_hint(
        "monitor_shape",
        "eightfold",
        url_patterns=(r"(?:[\w-]+)\.eightfold\.ai",),
        has_update_time=True,
    ),
)
