"""Workable detail API scraper ported from jobseek."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.monitors.shared import normalize_job_location_type

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.workable")

_JOB_URL_RE = re.compile(r"apply\.workable\.com/([\w-]+)/j/([\w]+)")


def _parse_job_url(url: str) -> tuple[str, str] | None:
    """Extract (slug, shortcode) from a Workable job URL."""
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _detail_url(slug: str, shortcode: str) -> str:
    """Build the Workable detail API URL."""
    return f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"


def _build_description(detail: dict[str, Any]) -> str | None:
    """Combine description + requirements + benefits into a single HTML body."""
    parts: list[str] = []
    for key in ("description", "requirements", "benefits"):
        text = detail.get(key)
        if text and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts) if parts else None


def _build_locations(detail: dict[str, Any]) -> list[str] | None:
    """Build location strings from the locations array."""
    raw_locations = detail.get("locations")
    if not raw_locations or not isinstance(raw_locations, list):
        loc = detail.get("location")
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
            return [name] if name else None
        if isinstance(loc, str) and loc:
            return [loc]
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for loc in raw_locations:
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
        elif isinstance(loc, str):
            name = loc
        else:
            continue
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    return locations or None


def _parse_job_location_type(detail: dict[str, Any]) -> str | None:
    """Derive job_location_type from workplace or remote fields."""
    workplace = detail.get("workplace")
    if isinstance(workplace, str):
        mapped = normalize_job_location_type(workplace, default=None)
        if mapped:
            return mapped
    if detail.get("remote"):
        return "remote"
    return None


def _parse_detail(detail: dict[str, Any]) -> ScrapedPostingPayload:
    """Parse the Workable detail API response into ScrapedPostingPayload."""
    title = detail.get("title")
    description = _build_description(detail)

    raw_type = detail.get("type")
    employment_type = raw_type if isinstance(raw_type, str) and raw_type else None

    metadata: dict[str, Any] | None = None
    dept = detail.get("department")
    if isinstance(dept, str) and dept:
        metadata = {"department": dept}
    elif isinstance(dept, list) and dept:
        metadata = {"department": ", ".join(dept)}

    return ScrapedPostingPayload(
        title=title,
        description=description,
        locations=_build_locations(detail),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(detail),
        date_posted=detail.get("published"),
        metadata=metadata,
    )


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    """Fetch job details from the Workable detail API."""
    parsed = _parse_job_url(url)
    if not parsed:
        logger.warning("workable.unparseable_url", url=url)
        return None

    slug, shortcode = parsed
    slug = config.get("token") or slug
    api_url = _detail_url(slug, shortcode)

    try:
        resp = await http.get(api_url)
        resp.raise_for_status()
        return _parse_detail(resp.json())
    except Exception as exc:
        logger.error("workable.detail_failed", url=url, error=str(exc))
        return None


register_scraper("workable", scrape)
