from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from job_ftch.domain.site_models import MonitorResult

if TYPE_CHECKING:
    import httpx

MAX_JOBS = 50_000


class BoardGoneError(Exception):
    """The upstream ATS confirms the board no longer exists."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


def slugs_from_url(url: str) -> list[str]:
    """Derive candidate ATS board slugs from a URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return [parts[-2]]
    return [parts[0]] if parts else []


async def fetch_page_text(
    url: str,
    client: httpx.AsyncClient,
    max_chars: int = 500_000,
) -> str | None:
    """Fetch a page and return its text content (capped). Raises exceptions on network or HTTP errors."""
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp.text[:max_chars]


def truncated_rich_result(payloads: list[Any]) -> MonitorResult:
    """Cap a rich result and set the truncated flag."""
    capped = payloads[:MAX_JOBS]
    return MonitorResult(
        urls={p.url for p in capped},
        payloads_by_url={p.url: p for p in capped},
        truncated=len(payloads) > MAX_JOBS,
    )


def truncated_url_result(urls: set[str]) -> MonitorResult:
    """Cap a URL-only result and set the truncated flag."""
    capped = sorted(list(urls))[:MAX_JOBS]
    return MonitorResult(
        urls=set(capped),
        truncated=len(urls) > MAX_JOBS,
    )


def normalize_job_location_type(value: str, default: str | None = "onsite") -> str | None:
    """Map string location types to domain enums (remote, hybrid, onsite)."""
    if not value:
        return default
    v = value.lower()
    if "remote" in v:
        return "remote"
    if "hybrid" in v:
        return "hybrid"
    if "onsite" in v or "on-site" in v or "office" in v:
        return "onsite"
    return default


def normalize_salary_unit(unit: str | None) -> str | None:
    """Normalize salary interval tokens."""
    if not unit:
        return None
    unit = unit.lower().strip()
    if unit in ("year", "yearly", "annually", "annual"):
        return "year"
    if unit in ("month", "monthly"):
        return "month"
    if unit in ("hour", "hourly"):
        return "hour"
    return unit
