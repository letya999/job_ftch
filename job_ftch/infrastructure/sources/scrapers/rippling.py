"""Rippling detail API scraper ported from jobseek."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.monitors.shared import (
    normalize_job_location_type,
    normalize_salary_unit,
)

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.rippling")

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

_JOB_URL_RE = re.compile(
    r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs/([\w-]+)"
)


def _extract_job_params(url: str) -> tuple[str, str] | None:
    """Extract (slug, uuid) from a Rippling job URL."""
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _detail_url(slug: str, uuid: str) -> str:
    """Build the Rippling detail API URL."""
    return f"{_API_BASE}/{slug}/jobs/{uuid}"


def _parse_salary(pay_ranges: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Extract salary from payRangeDetails."""
    if not pay_ranges:
        return None
    pr = pay_ranges[0]
    sal_min = pr.get("rangeStart")
    sal_max = pr.get("rangeEnd")
    if sal_min is None and sal_max is None:
        return None
    currency = pr.get("currency")
    unit = normalize_salary_unit(pr.get("frequency")) or "year"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job_location_type(locations: list[str] | None) -> str | None:
    """Infer job_location_type from workLocations strings."""
    if not locations:
        return None
    for loc in locations:
        text = loc.lower()
        for token in ("remote", "hybrid", "onsite", "on-site", "on site"):
            if token in text:
                mapped = normalize_job_location_type(token, default=None)
                if mapped:
                    return mapped
    return None


def _parse_employment_type(emp: dict[str, Any] | None) -> str | None:
    """Pass through the upstream Rippling employment-type label."""
    if not emp:
        return None
    return emp.get("label") or emp.get("id") or None


def _parse_detail(detail: dict[str, Any]) -> ScrapedPostingPayload:
    """Parse a Rippling detail API response into ScrapedPostingPayload."""
    desc_obj = detail.get("description") or {}
    parts: list[str] = []
    company_desc = desc_obj.get("company")
    if company_desc:
        parts.append(company_desc)
    role_desc = desc_obj.get("role")
    if role_desc:
        parts.append(role_desc)
    description = "\n".join(parts) if parts else None

    work_locations = detail.get("workLocations") or []
    locations = [loc for loc in work_locations if loc] or None

    metadata: dict[str, Any] = {}
    dept = detail.get("department")
    if isinstance(dept, dict):
        dept_name = dept.get("name")
        if dept_name:
            metadata["department"] = dept_name
        base_dept = dept.get("base_department")
        if base_dept and base_dept != dept_name:
            metadata["base_department"] = base_dept
    company_name = detail.get("companyName")
    if company_name:
        metadata["company"] = company_name

    return ScrapedPostingPayload(
        title=detail.get("name"),
        description=description,
        locations=locations,
        employment_type=_parse_employment_type(detail.get("employmentType")),
        job_location_type=_parse_job_location_type(locations),
        date_posted=detail.get("createdOn"),
        base_salary=_parse_salary(detail.get("payRangeDetails")),
        metadata=metadata or None,
    )


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    """Fetch job details from the Rippling detail API."""
    parsed = _extract_job_params(url)
    if not parsed:
        logger.warning("rippling.unparseable_url", url=url)
        return None

    _url_slug, uuid = parsed
    slug = config.get("slug") or _url_slug
    api_url = _detail_url(slug, uuid)

    try:
        resp = await http.get(api_url)
        resp.raise_for_status()
        return _parse_detail(resp.json())
    except Exception as exc:
        logger.error("rippling.detail_failed", url=url, error=str(exc))
        return None


register_scraper("rippling", scrape)
