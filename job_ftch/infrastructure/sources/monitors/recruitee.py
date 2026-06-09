"""Recruitee Careers Site API monitor ported from jobseek."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    BoardGoneError,
    fetch_page_text,
    normalize_salary_unit,
    truncated_rich_result,
)
from job_ftch.infrastructure.sources.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.monitors.recruitee")

_DOMAIN_RE = re.compile(r"^([\w-]+)\.recruitee\.com$")
_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support", "status"})


def _slug_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_base_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = parsed.hostname
    if host:
        return f"{parsed.scheme or 'https'}://{host}"
    return None


def _api_url(api_base: str) -> str:
    return f"{api_base.rstrip('/')}/api/offers"


def _parse_job(offer: dict) -> DiscoveredPostingPayload | None:
    url = offer.get("careers_url")
    if not url:
        return None

    parts: list[str] = []
    if desc := offer.get("description"):
        parts.append(desc)
    if reqs := offer.get("requirements"):
        parts.append(reqs)
    description = "\n".join(parts) if parts else None

    locations: list[str] = []
    for loc in offer.get("locations", []):
        city = loc.get("city", "")
        country = loc.get("country", "")
        parts = [p for p in (city, country) if p]
        if name := ", ".join(parts):
            locations.append(name)

    job_location_type: str | None = None
    if offer.get("remote"):
        job_location_type = "remote"
    elif offer.get("hybrid"):
        job_location_type = "hybrid"

    salary = offer.get("salary")
    base_salary = None
    if isinstance(salary, dict):
        base_salary = {
            "currency": salary.get("currency"),
            "min": salary.get("min"),
            "max": salary.get("max"),
            "unit": normalize_salary_unit(salary.get("period")) or "year",
        }

    return DiscoveredPostingPayload(
        url=url,
        title=offer.get("title"),
        description=description,
        locations=locations or None,
        employment_type=offer.get("employment_type_code"),
        job_location_type=job_location_type,
        date_posted=offer.get("published_at"),
        base_salary=base_salary,
    )


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> MonitorResult | list[DiscoveredPostingPayload]:
    board_url = spec.url
    metadata = spec.monitor_config
    api_base = metadata.get("api_base") or _api_base_from_url(board_url)

    if not api_base:
        raise ValueError(f"Cannot derive Recruitee API base from {board_url!r}")

    url = _api_url(api_base)
    response = await client.get(url, follow_redirects=True)
    if response.status_code == 404:
        raise BoardGoneError(f"Recruitee API returned 404 for {url!r}", url=url)
    response.raise_for_status()

    data = response.json()
    raw_offers = data.get("offers", [])

    jobs: list[DiscoveredPostingPayload] = []
    for raw in raw_offers:
        if raw.get("status") != "published":
            continue
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    slug = _slug_from_url(url)
    if slug:
        return {"slug": slug, "api_base": f"https://{slug}.recruitee.com"}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html and (".recruitee.com" in html or "window.recruitee" in html):
        api_base = _api_base_from_url(url)
        if api_base:
            return {"api_base": api_base}

    return None


register_monitor("recruitee", discover, cost=10, rich=True, can_handle=can_handle)
