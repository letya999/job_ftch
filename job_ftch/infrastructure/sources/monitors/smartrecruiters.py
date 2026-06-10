"""SmartRecruiters Posting API monitor ported from jobseek."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    slugs_from_url,
    truncated_url_result,
)

if TYPE_CHECKING:
    import httpx

    from job_ftch.infrastructure.sources.site_models import MonitorResult

logger = logging.getLogger("job_ftch.monitors.smartrecruiters")

PAGE_SIZE = 100
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
    re.compile(r"careers\.smartrecruiters\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v1", "js", "css", "assets", "postings", "companies"})


def _has_smartrecruiters_signal(url: str, html: str | None) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if "smartrecruiters.com" in host:
        return True
    if not html:
        return False
    lowered = html.lower()
    return "smartrecruiters.com" in lowered or "smartrecruiters" in lowered


def _token_from_url(board_url: str) -> str | None:
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(token: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"


def _posting_url(token: str, posting_id: str) -> str:
    return f"https://jobs.smartrecruiters.com/{token}/{posting_id}"


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("SmartRecruiters API returned non-dict body")
                return data
            resp.raise_for_status()
        except Exception:
            if attempt < retries - 1:
                delay = base_delay * (2**attempt) * (0.5 + random.random())
                await asyncio.sleep(delay)
                continue
            raise
    return {}


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> MonitorResult | set[str]:
    board_url = spec.url
    metadata = spec.monitor_config
    token = metadata.get("token") or _token_from_url(board_url)

    if not token:
        raise ValueError(f"Cannot derive SmartRecruiters token from {board_url!r}")

    urls: set[str] = set()
    offset = 0
    list_url = _api_list_url(token)
    truncated = False

    while True:
        data = await _get_page_with_retry(client, list_url, {"limit": PAGE_SIZE, "offset": offset})

        content = data.get("content", [])
        for item in content:
            pid = item.get("id")
            if pid:
                urls.add(_posting_url(token, str(pid)))

        total_found = data.get("totalFound", 0)
        offset += PAGE_SIZE

        if offset >= total_found or len(content) < PAGE_SIZE:
            break

        if len(urls) >= MAX_JOBS:
            logger.warning("smartrecruiters.truncated", token=token, total=len(urls), cap=MAX_JOBS)
            truncated = True
            break

    if truncated:
        return truncated_url_result(urls)
    return urls


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(_api_list_url(token), params={"limit": 1, "offset": 0})
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("totalFound")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    token = _token_from_url(url)
    if token:
        if client is None:
            return {"token": token}
        count = await _fetch_job_count(token, client)
        if count is not None:
            return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    try:
        resp = await client.get(url, follow_redirects=True)
        final_url = str(resp.url)
        html = resp.text if resp.status_code == 200 else None
    except Exception:
        final_url = url
        html = None

    signal = _has_smartrecruiters_signal(final_url, html)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    count = await _fetch_job_count(found, client)
                    if count is not None:
                        return {"token": found, "jobs": count}

    if not signal:
        return None

    for slug in slugs_from_url(url):
        count = await _fetch_job_count(slug, client)
        if count is not None:
            return {"token": slug, "jobs": count}

    return None


register_monitor("smartrecruiters", discover, cost=10, rich=False, can_handle=can_handle)
