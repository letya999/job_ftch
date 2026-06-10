"""Breezy HR monitor ported from jobseek."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    fetch_page_text,
    truncated_url_result,
)

if TYPE_CHECKING:
    import httpx

    from job_ftch.infrastructure.sources.site_models import MonitorResult

logger = logging.getLogger("job_ftch.monitors.breezy")

_BREEZY_DOMAIN_RE = re.compile(r"^([\w-]+)\.breezy\.hr$")
_IGNORE_SLUGS = frozenset({"www", "api", "app", "developer", "marketing"})


def _origin(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host.lower()}"


def _breezy_portal_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().strip(".")
    if host.endswith(".breezy.hr"):
        match = _BREEZY_DOMAIN_RE.match(host)
        if match and match.group(1) not in _IGNORE_SLUGS:
            return f"{parsed.scheme or 'https'}://{host}"
    return None


def _api_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/json"


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> MonitorResult | set[str]:
    board_url = spec.url
    portal_url = spec.monitor_config.get("portal_url") or _breezy_portal_from_url(board_url)

    if not portal_url:
        raise ValueError(f"Cannot derive Breezy portal URL from {board_url!r}")

    resp = await client.get(_api_url(portal_url), follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return set()

    urls: set[str] = set()
    for opening in data:
        raw = opening.get("url")
        if isinstance(raw, str) and raw.strip():
            urls.add(urljoin(f"{portal_url.rstrip('/')}/", raw.strip()))

    if len(urls) > MAX_JOBS:
        return truncated_url_result(urls)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    portal = _breezy_portal_from_url(url)
    if portal:
        return {"portal_url": portal}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html and (".breezy.hr" in html or "breezy-portal" in html):
        # Simplification: just return origin if signal found
        return {"portal_url": _origin(url)}

    return None


register_monitor("breezy", discover, cost=10, rich=False, can_handle=can_handle)
