"""Ashby Job Board API monitor ported from jobseek."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    BoardGoneError,
    fetch_page_text,
    normalize_job_location_type,
    normalize_salary_unit,
    slugs_from_url,
    truncated_rich_result,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.monitors.ashby")

_PAGE_PATTERNS = [
    re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "js", "css", "assets", "posting-api"})


def _parse_locations(job: dict) -> list[str] | None:
    locations: list[str] = []
    seen: set[str] = set()

    location = job.get("location")
    if location and isinstance(location, str):
        locations.append(location)
        seen.add(location)

    for loc in job.get("secondaryLocations", []):
        name = loc if isinstance(loc, str) else loc.get("location", "")
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    if not locations:
        address = job.get("address")
        if isinstance(address, dict):
            city = address.get("city", "")
            region = address.get("region", "")
            country = address.get("country", "")
            parts = [p for p in (city, region, country) if p]
            if parts:
                name = ", ".join(parts)
                locations.append(name)

    return locations or None


def _parse_compensation(job: dict, compensation: dict | None) -> dict | None:
    if not compensation:
        return None
    comp_tiers = compensation.get("compensationTierSummary", [])
    if not comp_tiers:
        return None

    tier_id = job.get("compensationTierSummary")
    if not tier_id:
        return None

    for tier in comp_tiers:
        if tier.get("id") == tier_id:
            sal_min = tier.get("min")
            sal_max = tier.get("max")
            currency = tier.get("currency")
            if sal_min is None and sal_max is None:
                return None
            unit = normalize_salary_unit(tier.get("interval")) or "year"
            return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}

    return None


def _parse_job(job: dict, compensation: dict | None = None) -> DiscoveredPostingPayload | None:
    url = job.get("jobUrl")
    if not url:
        return None

    metadata: dict = {}
    department = job.get("department")
    if department:
        metadata["department"] = department
    team = job.get("team")
    if team:
        metadata["team"] = team
    job_id = job.get("id")
    if job_id:
        metadata["id"] = job_id

    workplace_type = job.get("workplaceType")
    job_location_type = (
        normalize_job_location_type(workplace_type, default=None) if workplace_type else None
    )

    return DiscoveredPostingPayload(
        url=url,
        title=job.get("title"),
        description=job.get("descriptionHtml") or job.get("descriptionPlain"),
        locations=_parse_locations(job),
        employment_type=job.get("employmentType"),
        job_location_type=job_location_type,
        date_posted=job.get("publishedAt"),
        base_salary=_parse_compensation(job, compensation),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    match = re.search(r"jobs\.ashbyhq\.com/([\w-]+)", board_url)
    if match and match.group(1) not in _IGNORE_TOKENS:
        return match.group(1)
    return None


def _api_url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(_api_url(token))
        if resp.status_code != 200:
            return None
        data = resp.json()
        jobs = data.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    except Exception:
        return None


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> MonitorResult | list[DiscoveredPostingPayload]:
    board_url = spec.url
    token = spec.monitor_config.get("token") or _token_from_url(board_url)

    if not token:
        raise ValueError(f"Cannot derive Ashby token from {board_url!r}")

    url = _api_url(token)
    params = {"includeCompensation": "true"}
    response = await client.get(url, params=params)
    if response.status_code == 404:
        raise BoardGoneError(f"Ashby board token {token!r} returned 404", url=str(response.url))
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])
    compensation = data.get("compensation")

    jobs: list[DiscoveredPostingPayload] = []
    for raw in raw_jobs:
        if not raw.get("isListed", True):
            continue
        parsed = _parse_job(raw, compensation)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        logger.warning("ashby.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    return jobs


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


register_monitor("ashby", discover, cost=10, rich=True, can_handle=can_handle)
