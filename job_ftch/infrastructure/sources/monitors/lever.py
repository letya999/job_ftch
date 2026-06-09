"""Lever Postings API monitor ported from jobseek."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

import httpx

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    BoardGoneError,
    fetch_page_text,
    slugs_from_url,
    truncated_rich_result,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)

logger = logging.getLogger("job_ftch.monitors.lever")

BATCH_SIZE = 100
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"api\.(?:eu\.)?lever\.co/v0/postings/([\w-]+)"),
    re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
]

_EU_DOMAIN = re.compile(r"(?:api|jobs)\.eu\.lever\.co/")
_IGNORE_TOKENS = frozenset({"v0", "api", "js", "css", "assets"})
_API_HEADERS = {"Accept": "application/json"}


def _build_description(posting: dict) -> str | None:
    parts: list[str] = []
    description = posting.get("description")
    if description:
        parts.append(description)
    for item in posting.get("lists", []):
        text = item.get("text", "")
        content = item.get("content", "")
        if text or content:
            parts.append(f"<h3>{text}</h3><ul>{content}</ul>")
    additional = posting.get("additional")
    if additional:
        parts.append(additional)
    return "\n".join(parts) if parts else None


def _parse_salary(salary_range: dict | None) -> dict | None:
    if not salary_range:
        return None
    currency = salary_range.get("currency")
    sal_min = salary_range.get("min")
    sal_max = salary_range.get("max")
    if sal_min is None and sal_max is None:
        return None
    return {
        "currency": currency,
        "min": sal_min,
        "max": sal_max,
        "unit": salary_range.get("interval"),
    }


def _parse_job(posting: dict) -> DiscoveredPostingPayload | None:
    url = posting.get("hostedUrl")
    if not url:
        return None

    categories = posting.get("categories", {})
    all_locations = categories.get("allLocations", [])
    if not all_locations:
        single = categories.get("location")
        all_locations = [single] if single else []

    metadata: dict = {}
    team = categories.get("team")
    if team:
        metadata["team"] = team
    department = categories.get("department")
    if department:
        metadata["department"] = department
    posting_id = posting.get("id")
    if posting_id:
        metadata["id"] = posting_id

    return DiscoveredPostingPayload(
        url=url,
        title=posting.get("text"),
        description=_build_description(posting),
        locations=all_locations or None,
        employment_type=categories.get("commitment"),
        job_location_type=posting.get("workplaceType"),
        base_salary=_parse_salary(posting.get("salaryRange")),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    match = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", board_url)
    if match and match.group(1) not in _IGNORE_TOKENS:
        return match.group(1)
    return None


def _region_from_url(url: str) -> str | None:
    return "eu" if _EU_DOMAIN.search(url) else None


def _api_url(token: str, region: str | None = None) -> str:
    if region == "eu":
        return f"https://api.eu.lever.co/v0/postings/{token}"
    return f"https://api.lever.co/v0/postings/{token}"


async def _fetch_job_count(
    token: str, client: httpx.AsyncClient, region: str | None = None
) -> int | str | None:
    try:
        resp = await client.get(
            _api_url(token, region), params={"limit": 100}, headers=_API_HEADERS
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list):
            return "100+" if len(data) >= 100 else len(data)
        return None
    except Exception:
        return None


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> list[dict]:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params, headers=_API_HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, list):
                    raise ValueError("Lever API returned non-list body")
                return data
            if resp.status_code == 404:
                raise httpx.HTTPStatusError("Not Found", request=resp.request, response=resp)
            resp.raise_for_status()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                delay = base_delay * (2**attempt) * (0.5 + random.random())
                await asyncio.sleep(delay)
                continue
            raise last_exc from None

    return []


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | list[DiscoveredPostingPayload]:
    board_url = spec.url
    token = spec.monitor_config.get("token") or _token_from_url(board_url)

    if not token:
        raise ValueError(f"Cannot derive Lever token from {board_url!r}")

    region = spec.monitor_config.get("region") or _region_from_url(board_url)
    url = _api_url(token, region)
    jobs: list[DiscoveredPostingPayload] = []
    skip = 0

    while True:
        try:
            batch = await _get_page_with_retry(client, url, {"limit": BATCH_SIZE, "skip": skip})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and skip == 0:
                raise BoardGoneError(f"Lever board token {token!r} returned 404", url=url) from exc
            raise

        for raw in batch:
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        if len(batch) < BATCH_SIZE:
            break

        skip += BATCH_SIZE
        if len(jobs) >= MAX_JOBS:
            logger.warning("lever.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
            return truncated_rich_result(jobs)

        await asyncio.sleep(0.5)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    region = _region_from_url(url)
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client, region)
            if count is not None:
                result: dict[str, Any] = {"token": token, "jobs": count}
                if region:
                    result["region"] = region
                return result
        result = {"token": token}
        if region:
            result["region"] = region
        return result

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    html_region = region
                    if not html_region and ".eu.lever.co" in match.group(0):
                        html_region = "eu"
                    count = await _fetch_job_count(found, client, html_region)
                    result = {"token": found}
                    if html_region:
                        result["region"] = html_region
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        count = await _fetch_job_count(slug, client, region)
        if count is not None:
            result = {"token": slug, "jobs": count}
            if region:
                result["region"] = region
            return result

    return None


register_monitor("lever_board", discover, cost=10, rich=True, can_handle=can_handle)
