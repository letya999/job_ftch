"""Workday detail API scraper ported from jobseek."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_scraper
from job_ftch.domain.site_models import ScrapedPostingPayload
from job_ftch.infrastructure.sources.monitors.shared import normalize_job_location_type

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.scrapers.workday")

_JOB_URL_RE = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?"
    r"([^/]+)"
    r"(/job/.+)"
)


def _parse_job_url(url: str) -> tuple[str, str, str, str] | None:
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    company = match.group(1)
    wd_instance = f"wd{match.group(2)}"
    site = match.group(3)
    path = match.group(4)
    return company, wd_instance, site, path


def _detail_url(company: str, wd_instance: str, site: str, path: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}{path}"


def _normalize_workday_location(raw: str) -> str:
    cleaned = re.sub(r"\s*~.*", "", raw).strip()
    if not cleaned:
        return raw

    # Display format: Workday uses double spaces as segment separators
    if "  " in cleaned:
        return ", ".join(part.strip() for part in cleaned.split("  ") if part.strip())
    return cleaned


def _parse_detail(data: dict[str, Any]) -> ScrapedPostingPayload:
    info = data.get("jobPostingInfo", {})

    locations: list[str] = []
    primary = info.get("location")
    additional = info.get("additionalLocations") or []
    for loc in [primary, *additional]:
        if loc:
            locations.append(_normalize_workday_location(loc))

    metadata: dict[str, Any] = {}
    if req_id := info.get("jobReqId"):
        metadata["jobReqId"] = req_id

    return ScrapedPostingPayload(
        title=info.get("title"),
        description=info.get("jobDescription"),
        locations=locations or None,
        employment_type=info.get("timeType"),
        job_location_type=normalize_job_location_type(info.get("remoteType"), default=None),
        date_posted=info.get("startDate"),
        metadata=metadata or None,
    )


async def scrape(
    url: str, config: dict[str, Any], http: httpx.AsyncClient
) -> ScrapedPostingPayload | None:
    parsed = _parse_job_url(url)
    if not parsed:
        return None

    company, wd_instance, site, path = parsed
    api_url = _detail_url(company, wd_instance, site, path)

    resp = await http.get(api_url, headers={"Content-Type": "application/json"})
    if resp.status_code == 404:
        return None
    if resp.status_code == 403:
        try:
            if resp.json().get("errorCode") == "S22":
                return None
        except Exception:
            pass
    resp.raise_for_status()

    return _parse_detail(resp.json())


register_scraper("workday", scrape)
