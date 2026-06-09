"""Rippling ATS Job Board API monitor ported from jobseek."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    fetch_page_text,
    slugs_from_url,
    truncated_url_result,
)

if TYPE_CHECKING:
    import httpx

    from job_ftch.infrastructure.sources.site_models import MonitorResult

logger = logging.getLogger("job_ftch.monitors.rippling")

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"
_URL_RE = re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs")
_IGNORE_SLUGS = frozenset({"api", "platform", "static", "assets", "js", "css"})


def _slug_from_url(board_url: str) -> str | None:
    match = _URL_RE.search(board_url)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_list_url(slug: str) -> str:
    return f"{_API_BASE}/{slug}/jobs"


def _posting_url(slug: str, uuid: str) -> str:
    return f"https://ats.rippling.com/{slug}/jobs/{uuid}"


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | set[str]:
    board_url = spec.url
    metadata = spec.monitor_config
    slug = metadata.get("slug") or _slug_from_url(board_url)

    if not slug:
        raise ValueError(f"Cannot derive Rippling board slug from {board_url!r}")

    resp = await client.get(_api_list_url(slug))
    resp.raise_for_status()

    job_list: list[dict] = resp.json()
    if not isinstance(job_list, list):
        return set()

    uuids = [j["uuid"] for j in job_list if j.get("uuid")]

    if len(uuids) > MAX_JOBS:
        return truncated_url_result({_posting_url(slug, uuid) for uuid in uuids})

    return {_posting_url(slug, uuid) for uuid in uuids}


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    try:
        resp = await client.get(_api_list_url(slug))
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_slug(slug, client)
            if found:
                result: dict[str, Any] = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in [_URL_RE]:
            match = pattern.search(html)
            if match:
                found_slug = match.group(1)
                if found_slug not in _IGNORE_SLUGS:
                    found, count = await _probe_slug(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    for slug_candidate in slugs_from_url(url):
        found, count = await _probe_slug(slug_candidate, client)
        if found:
            result = {"slug": slug_candidate}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register_monitor("rippling", discover, cost=10, rich=False, can_handle=can_handle)
