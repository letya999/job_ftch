"""Deel ATS Job Board API monitor.

Public API (api-prod.letsdeel.com):
- Settings: GET /guest/ats/organizations/{slug}/career_page_settings
- Postings: GET /guest/ats/organizations/{org_id}/job_boards/{board_id}/job_postings

Board page lives at https://jobs.deel.com/{slug}; individual postings at
https://jobs.deel.com/{slug}/job-details/{posting_id}/overview.

Returns full job data (title, rich-text description, locations, salary, employment type).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_monitor
from job_ftch.domain.site_models import DiscoveredPostingPayload, MonitorResult

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_FRONTEND_BASE = "https://jobs.deel.com"
_API_BASE = "https://api-prod.letsdeel.com"
_SETTINGS = f"{_API_BASE}/guest/ats/organizations/{{slug}}/career_page_settings"
_POSTINGS = f"{_API_BASE}/guest/ats/organizations/{{org_id}}/job_boards/{{board_id}}/job_postings"

_DEEL_RE = re.compile(r"jobs\.deel\.com/(?:job-boards/)?([\w-]+)")
_IGNORE_SLUGS = frozenset(
    {"auth", "login", "signup", "guest", "api", "deelapi", "job-boards", "job-details"}
)
MAX_JOBS = 500


def _parse_salary(posting: dict[str, Any]) -> dict[str, Any] | None:
    if not posting.get("isCompensationVisible", False):
        return None
    comp = (posting.get("job") or {}).get("currentCompensation")
    if not comp:
        return None
    sal_min = comp.get("minAmount")
    sal_max = comp.get("maxAmount")
    if sal_min is None and sal_max is None:
        return None
    return {
        "currency": comp.get("currencyIsoCode"),
        "min": sal_min,
        "max": sal_max,
        "unit": "year",
    }


def _parse_job(posting: dict[str, Any], slug: str) -> DiscoveredPostingPayload | None:
    posting_id = posting.get("id")
    if not posting_id:
        return None

    url = f"{_FRONTEND_BASE}/{slug}/job-details/{posting_id}/overview"
    job = posting.get("job") or {}

    # Locations
    locations = [
        loc["name"]
        for jl in job.get("jobLocations") or []
        if (loc := jl.get("location")) and loc.get("name")
    ]

    # Employment type — take first
    emp_type = None
    for jet in job.get("jobEmploymentTypes") or []:
        name = (jet.get("employmentType") or {}).get("name")
        if name:
            emp_type = name
            break

    # Metadata: team + department
    metadata: dict[str, Any] = {}
    teams = [
        t["name"] for jt in job.get("jobTeams") or [] if (t := jt.get("team")) and t.get("name")
    ]
    if teams:
        metadata["team"] = ", ".join(teams)
    departments = [
        d["name"]
        for jd in job.get("jobDepartments") or []
        if (d := jd.get("department")) and d.get("name")
    ]
    if departments:
        metadata["department"] = ", ".join(departments)
    if posting_id:
        metadata["id"] = posting_id

    return DiscoveredPostingPayload(
        url=url,
        title=posting.get("title"),
        description=posting.get("richtextDescription"),
        locations=locations or None,
        employment_type=emp_type,
        base_salary=_parse_salary(posting),
        date_posted=posting.get("createdAt"),
        metadata=metadata or None,
    )


async def _fetch_settings(slug: str, client: httpx.AsyncClient) -> dict[str, Any] | None:
    from job_ftch.config import get_settings

    try:
        resp = await client.get(
            _SETTINGS.format(slug=slug), timeout=get_settings().monitor_timeout_seconds
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _ids_from_settings(settings: dict[str, Any]) -> tuple[str | None, str | None]:
    org_id = settings.get("organizationId")
    board_id = (settings.get("jobBoard") or {}).get("id")
    return org_id, board_id


async def discover(
    spec: Any,
    client: httpx.AsyncClient,
    auth: Any = None,
) -> list[DiscoveredPostingPayload] | MonitorResult:
    """Fetch all job postings from the Deel API."""
    board_url = spec.url
    config = spec.monitor_config or {}
    slug = config.get("slug")
    org_id = config.get("org_id")
    board_id = config.get("board_id")

    if not slug:
        m = _DEEL_RE.search(board_url)
        if m and m.group(1) not in _IGNORE_SLUGS:
            slug = m.group(1)

    if not slug:
        log.warning("deel.missing_slug", url=board_url)
        return []

    if not org_id or not board_id:
        settings = await _fetch_settings(slug, client)
        if not settings:
            log.warning("deel.settings_fetch_failed", slug=slug)
            return []
        org_id, board_id = _ids_from_settings(settings)

    if not org_id or not board_id:
        log.warning("deel.missing_ids", slug=slug, org_id=org_id, board_id=board_id)
        return []

    url = _POSTINGS.format(org_id=org_id, board_id=board_id)
    from job_ftch.config import get_settings

    resp = await client.get(url, timeout=get_settings().monitor_timeout_seconds)
    resp.raise_for_status()
    data = resp.json()

    postings = data if isinstance(data, list) else data.get("jobPostings") or []
    jobs = [j for raw in postings if isinstance(raw, dict) and (j := _parse_job(raw, slug))]

    if not jobs:
        return MonitorResult(metadata_updates={"confirmed_empty": True})

    if len(jobs) > MAX_JOBS:
        log.info("deel.truncated", count=len(jobs), cap=MAX_JOBS)
        jobs = jobs[:MAX_JOBS]

    log.info("deel.discovered", slug=slug, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Detect Deel: domain match + settings API probe."""
    m = _DEEL_RE.search(url)
    if not m:
        return None

    slug = m.group(1)
    if slug in _IGNORE_SLUGS:
        return None

    if client is None:
        return {"slug": slug}

    settings = await _fetch_settings(slug, client)
    if not settings:
        return None

    org_id, board_id = _ids_from_settings(settings)
    if not org_id or not board_id:
        return None

    return {
        "slug": slug,
        "org_id": org_id,
        "board_id": board_id,
    }


register_monitor(
    "deel",
    discover,
    cost=20,
    rich=True,
    can_handle=can_handle,
    assessment_hint=known_board_assessment_hint(
        "monitor_shape",
        "deel",
        url_patterns=(r"jobs\.deel\.com/(?:job-boards/)?[\w-]+",),
        has_publication_time=True,
        has_stable_id=True,
        can_detect_freshness_without_snapshot=True,
        can_filter_since_yesterday=True,
        item_level_dates=True,
        requires_full_snapshot=False,
        rationale="Deel board API returns stable job IDs and createdAt timestamps.",
    ),
)
