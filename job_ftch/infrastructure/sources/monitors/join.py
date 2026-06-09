"""JOIN (join.com) monitor — pre-configured nextdata monitor.

JOIN career pages are Next.js apps with job data embedded in
__NEXT_DATA__ at props.pageProps.initialState.jobs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text
from job_ftch.infrastructure.sources.monitors.nextdata import discover as nextdata_discover
from job_ftch.infrastructure.sources.nextdata_utils import extract_next_data, resolve_path

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_SLUG_RE = re.compile(r"^/companies/([\w-]+)")


def _slug_from_url(url: str) -> str | None:
    """Extract the company slug from a join.com URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("join.com", "www.join.com"):
        return None
    match = _SLUG_RE.match(parsed.path)
    return match.group(1) if match else None


def _build_metadata(slug: str) -> dict[str, Any]:
    """Build the full nextdata metadata dict for a JOIN company."""
    return {
        "path": "props.pageProps.initialState.jobs.items",
        "url_template": f"https://join.com/companies/{slug}/{{idParam}}",
        "pagination": {
            "path": "props.pageProps.initialState.jobs.pagination",
            "page_count": "pageCount",
            "page_param": "page",
        },
    }


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    """Discover jobs from a JOIN career page via nextdata."""
    board_url = spec.url
    config = spec.monitor_config or {}

    slug = config.get("slug") or _slug_from_url(board_url)
    if not slug:
        log.error("join.missing_slug", board_url=board_url)
        return set()

    # Create a mock spec for nextdata monitor
    from dataclasses import dataclass
    @dataclass
    class MockSpec:
        url: str
        monitor_config: dict[str, Any]

    nextdata_spec = MockSpec(
        url=board_url,
        monitor_config=_build_metadata(slug),
    )
    result = await nextdata_discover(nextdata_spec, client, auth=auth)
    if isinstance(result, set):
        return result
    log.warning("join.unexpected_result_type", type=type(result))
    return set()


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Detect a join.com career page."""
    slug = _slug_from_url(url)
    if not slug:
        return None

    if client is None:
        return {"slug": slug}

    # Verify the page has job data
    html = await fetch_page_text(url, client)
    if not html:
        return None
    data = extract_next_data(html)
    if not data:
        return None

    items = resolve_path(data, "props.pageProps.initialState.jobs.items")
    if not isinstance(items, list):
        return None

    pagination = resolve_path(data, "props.pageProps.initialState.jobs.pagination")
    total = pagination.get("total") if isinstance(pagination, dict) else len(items)

    log.info("join.detected", url=url, slug=slug, jobs=total)
    return {"slug": slug, "jobs": total}


register_monitor("join", discover, cost=40, rich=False, can_handle=can_handle)
