"""Workday Job Board API monitor ported from jobseek."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    fetch_page_text,
    truncated_url_result,
)

if TYPE_CHECKING:
    import httpx

    from job_ftch.infrastructure.sources.site_models import MonitorResult

logger = logging.getLogger("job_ftch.monitors.workday")

PAGE_SIZE = 20
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0

_URL_RE = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?"
    r"(.+?)/?$"
)


def _parse_components(url: str) -> tuple[str, str, str] | None:
    match = _URL_RE.search(url)
    if not match:
        return None
    company = match.group(1)
    wd_instance = f"wd{match.group(2)}"
    site = match.group(3)
    return company, wd_instance, site


def _api_list_url(company: str, wd_instance: str, site: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"


def _job_url(company: str, wd_instance: str, site: str, external_path: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/{site}{external_path}"


async def _post_page_with_retry(
    client: httpx.AsyncClient,
    list_url: str,
    payload: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    for attempt in range(retries):
        try:
            resp = await client.post(list_url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Workday API returned non-dict body")
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
    company = metadata.get("company")
    wd_instance = metadata.get("wd_instance")
    site = metadata.get("site")

    if not (company and wd_instance and site):
        parsed = _parse_components(board_url)
        if not parsed:
            raise ValueError(f"Cannot parse Workday components from {board_url!r}")
        company, wd_instance, site = parsed

    urls: set[str] = set()
    offset = 0
    list_url = _api_list_url(company, wd_instance, site)
    truncated = False

    while True:
        payload = {"limit": PAGE_SIZE, "offset": offset}
        data = await _post_page_with_retry(client, list_url, payload)

        postings = data.get("jobPostings", [])
        for item in postings:
            path = item.get("externalPath")
            if path:
                urls.add(_job_url(company, wd_instance, site, path))

        total = data.get("total", 0)
        offset += len(postings)

        if not postings or offset >= total:
            break

        if len(urls) >= MAX_JOBS:
            logger.warning("workday.truncated", company=company, total=len(urls), cap=MAX_JOBS)
            truncated = True
            break

    if truncated:
        return truncated_url_result(urls)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    parsed = _parse_components(url)
    if parsed:
        company, wd_instance, site = parsed
        return {"company": company, "wd_instance": wd_instance, "site": site}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        match = _URL_RE.search(html)
        if match:
            return {
                "company": match.group(1),
                "wd_instance": f"wd{match.group(2)}",
                "site": match.group(3),
            }
    return None


register_monitor("workday", discover, cost=10, rich=False, can_handle=can_handle)
