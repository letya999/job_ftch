"""Greenhouse JSON API monitor ported from jobseek."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import structlog

from job_ftch.application.registry import known_board_assessment_hint, register_monitor
from job_ftch.domain.site_models import (
    DiscoveredPostingPayload,
    MonitorResult,
)
from job_ftch.infrastructure.sources.monitors.shared import (
    MAX_JOBS,
    BoardGoneError,
    fetch_page_text,
    truncated_rich_result,
)

if TYPE_CHECKING:
    import httpx

logger = structlog.get_logger("job_ftch.monitors.greenhouse")

_PAGE_PATTERNS = [
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    re.compile(r"job-boards(?:\.[\w-]+)?\.greenhouse\.io/([\w-]+)"),
    re.compile(r'"urlToken":"([\w-]+)"'),
    re.compile(r"boardToken[\"']?\s*[:=]\s*[\"']([\w-]+)[\"']", re.IGNORECASE),
]

_IGNORE_TOKENS = frozenset({"embed", "v1", "api", "js", "css", "assets"})

_STRAY_TOGGLE_RE = re.compile(
    r"<a(?![^>]*\shref=)[^>]*>\s*(?:read|learn|show|see|view)\s+more\s*</a>",
    re.IGNORECASE,
)


def _clean_description(html: str | None) -> str | None:
    if not html:
        return html
    return _STRAY_TOGGLE_RE.sub("", html)


def _parse_job(job: dict[str, Any]) -> DiscoveredPostingPayload | None:
    url = job.get("absolute_url")
    if not url:
        return None

    locations: list[str] = []
    seen: set[str] = set()
    loc = job.get("location")
    if isinstance(loc, dict) and loc.get("name"):
        name = loc["name"]
        locations.append(name)
        seen.add(name)
    for office in job.get("offices", []):
        name = office.get("name")
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    metadata: dict[str, Any] = {}
    departments = [d.get("name") for d in job.get("departments", []) if d.get("name")]
    if departments:
        metadata["departments"] = departments
    if job.get("education"):
        metadata["education"] = job["education"]
    if job.get("requisition_id"):
        metadata["requisition_id"] = job["requisition_id"]

    return DiscoveredPostingPayload(
        url=url,
        title=job.get("title"),
        description=_clean_description(job.get("content")),
        locations=locations or None,
        date_posted=job.get("first_published"),
        language=job.get("language"),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    def _clean(token: str | None) -> str | None:
        if not token:
            return None
        token = token.strip()
        if not token:
            return None
        return token if token not in _IGNORE_TOKENS else None

    if host == "boards-api.greenhouse.io":
        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "boards":
            return _clean(parts[2])
        return None

    if host == "boards.greenhouse.io":
        if parts and parts[0] == "embed":
            return _clean(query.get("for", [None])[0])
        if parts:
            return _clean(parts[0])
        return None

    if re.fullmatch(r"job-boards(?:\.[\w-]+)?\.greenhouse\.io", host):
        if parts:
            return _clean(parts[0])
        return _clean(query.get("url_token", [None])[0] or query.get("for", [None])[0])

    return None


def _api_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    try:
        resp = await client.get(_api_url(token), params={"content": "false"})
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
    """Fetch job listings from Greenhouse API."""
    board_url = spec.url
    token = spec.monitor_config.get("token") or _token_from_url(board_url)

    if not token:
        raise ValueError(f"Cannot derive Greenhouse token from {board_url!r}")

    url = _api_url(token)
    response = await client.get(url, params={"content": "true"})
    if response.status_code == 404:
        raise BoardGoneError(
            f"Greenhouse board token {token!r} returned 404", url=str(response.url)
        )
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])

    jobs: list[DiscoveredPostingPayload] = []
    for raw in raw_jobs:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if not jobs:
        return MonitorResult(metadata_updates={"confirmed_empty": True})

    if len(jobs) > MAX_JOBS:
        logger.warning("greenhouse.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Detect Greenhouse boards."""
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

    return None


register_monitor(
    "greenhouse",
    discover,
    cost=10,
    rich=True,
    can_handle=can_handle,
    assessment_hint=known_board_assessment_hint(
        "monitor_shape",
        "greenhouse",
        url_patterns=(
            r"boards-api\.greenhouse\.io/v1/boards/[\w-]+",
            r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?[\w-]+",
            r"job-boards(?:\.[\w-]+)?\.greenhouse\.io/[\w-]+",
        ),
        has_publication_time=True,
        has_stable_id=True,
        can_detect_freshness_without_snapshot=True,
        can_filter_since_yesterday=True,
        item_level_dates=True,
        requires_full_snapshot=False,
        rationale="Greenhouse board API returns stable job IDs and first_published timestamps.",
    ),
)
