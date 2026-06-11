"""SmartRecruiters detail API scraper ported from jobseek."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.monitors.shared import normalize_salary_unit

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.smartrecruiters")

_JOB_URL_RE = re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/([\w-]+)")


def _parse_job_url(url: str) -> tuple[str | None, str | None]:
    """Extract (token, posting_id) from a SmartRecruiters job URL."""
    match = _JOB_URL_RE.search(url)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _detail_url(token: str, posting_id: str) -> str:
    """Build the SmartRecruiters detail API URL."""
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"


def _build_description(job_ad: dict[str, Any]) -> str | None:
    """Combine jobAd sections into a single HTML description."""
    if not job_ad:
        return None
    sections = job_ad.get("sections", {})
    parts: list[str] = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            title = section.get("title", "")
            text = section.get("text", "")
            if text:
                if title:
                    parts.append(f"<h3>{title}</h3>\n{text}")
                else:
                    parts.append(text)
    return "\n".join(parts) if parts else None


def _build_location(loc: dict[str, Any]) -> str | None:
    """Build a human-readable location string."""
    if not loc:
        return None
    full = loc.get("fullLocation")
    if full:
        return str(full)
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    filtered = [p for p in parts if p]
    return str(", ".join(str(p) for p in filtered)) if filtered else None


def _parse_salary(posting: dict[str, Any]) -> dict[str, Any] | None:
    """Extract salary from the compensation field if available."""
    comp = posting.get("compensation")
    if not comp:
        return None
    salary = comp.get("salary")
    if not salary:
        return None
    sal_min = salary.get("min")
    sal_max = salary.get("max")
    if sal_min is None and sal_max is None:
        return None
    currency = salary.get("currency")
    unit = normalize_salary_unit(salary.get("period")) or "year"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_detail(posting: dict[str, Any]) -> ScrapedPostingPayload:
    """Parse a SmartRecruiters detail API response into ScrapedPostingPayload."""
    title = posting.get("name")
    description = _build_description(posting.get("jobAd", {}))

    loc = posting.get("location", {})
    location_str = _build_location(loc)
    locations = [location_str] if location_str else None

    job_location_type = None
    if loc.get("remote"):
        job_location_type = "remote"
    elif loc.get("hybrid"):
        job_location_type = "hybrid"

    employment = posting.get("typeOfEmployment")
    employment_type = employment.get("label") if isinstance(employment, dict) else None

    metadata: dict[str, Any] = {}
    dept = posting.get("department")
    if isinstance(dept, dict) and dept.get("label"):
        metadata["department"] = dept["label"]
    func = posting.get("function")
    if isinstance(func, dict) and func.get("label"):
        metadata["function"] = func["label"]
    exp = posting.get("experienceLevel")
    if isinstance(exp, dict) and exp.get("label"):
        metadata["experienceLevel"] = exp["label"]

    return ScrapedPostingPayload(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=posting.get("releasedDate"),
        base_salary=_parse_salary(posting),
        metadata=metadata or None,
    )


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    """Fetch job details from the SmartRecruiters detail API."""
    token, posting_id = _parse_job_url(url)
    if not token or not posting_id:
        logger.warning("smartrecruiters.unparseable_url", url=url)
        return None

    api_url = _detail_url(token, posting_id)
    try:
        resp = await http.get(api_url)
        resp.raise_for_status()
        return _parse_detail(resp.json())
    except Exception as exc:
        logger.error("smartrecruiters.detail_failed", url=url, error=str(exc))
        return None


register_scraper("smartrecruiters", scrape)
