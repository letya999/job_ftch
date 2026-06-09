from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DiscoveredPostingPayload:
    """Output of a BoardMonitor. Either URL-only or rich (full fields)."""

    url: str
    title: str | None = None
    description: str | None = None  # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict[str, Any] | None = None
    language: str | None = None
    localizations: dict[str, Any] | None = None
    extras: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class ScrapedPostingPayload:
    """Output of a JobScraper. Same shape as DiscoveredPostingPayload minus url."""

    title: str | None = None
    description: str | None = None  # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict[str, Any] | None = None
    language: str | None = None
    extras: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class MonitorResult:
    """Normalized result from a monitor run."""

    urls: set[str] = field(default_factory=set)
    payloads_by_url: dict[str, DiscoveredPostingPayload] | None = None
    metadata_updates: dict[str, Any] | None = None
    hybrid: bool = False  # partial-rich: some URLs have data, others don't
    truncated: bool = False  # hit MAX_JOBS cap; pipeline skips tombstone logic
    filtered_count: int = 0
