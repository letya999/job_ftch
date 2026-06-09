"""Workable Posting API monitor ported from jobseek."""

from __future__ import annotations

import asyncio
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

logger = logging.getLogger("job_ftch.monitors.workable")

_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF = (5.0, 15.0, 30.0, 60.0)
_PAGE_PATTERNS = [re.compile(r"apply\.workable\.com/([\w-]+)")]
_IGNORE_TOKENS = frozenset({"api", "v2", "v3", "js", "css", "assets", "accounts", "jobs"})


def _token_from_url(board_url: str) -> str | None:
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(slug: str) -> str:
    return f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"


def _job_url(slug: str, shortcode: str) -> str:
    return f"https://apply.workable.com/{slug}/j/{shortcode}/"


async def _api_list(slug: str, client: httpx.AsyncClient) -> tuple[set[str], bool]:
    urls: set[str] = set()
    truncated = False
    body: dict = {"query": "", "location": [], "department": [], "worktype": []}

    while True:
        data = None
        for attempt in range(_RETRY_ATTEMPTS):
            resp = await client.post(_api_list_url(slug), json=body)
            if resp.status_code == 429:
                backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                await asyncio.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            break

        if data is None:
            break

        results = data.get("results", [])
        for item in results:
            sc = item.get("shortcode")
            if sc:
                urls.add(_job_url(slug, sc))

        next_page = data.get("nextPage")
        if not next_page or not results:
            break

        body["token"] = next_page
        if len(urls) >= MAX_JOBS:
            truncated = True
            break

    return urls, truncated


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | set[str]:
    board_url = spec.url
    metadata = spec.monitor_config
    slug = metadata.get("token") or _token_from_url(board_url)

    if not slug:
        raise ValueError(f"Cannot derive Workable slug from {board_url!r}")

    urls, truncated = await _api_list(slug, client)
    if truncated:
        return truncated_url_result(urls)
    return urls


async def _fetch_job_count(slug: str, client: httpx.AsyncClient) -> int | None:
    try:
        body = {"query": "", "location": [], "department": [], "worktype": []}
        resp = await client.post(_api_list_url(slug), json=body)
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client)
            if count is not None:
                return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    count = await _fetch_job_count(found, client)
                    result: dict[str, Any] = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        count = await _fetch_job_count(slug, client)
        if count is not None:
            return {"token": slug, "jobs": count}

    return None


register_monitor("workable", discover, cost=10, rich=False, can_handle=can_handle)
