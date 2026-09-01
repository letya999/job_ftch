"""Domain models for pre-ingest source freshness assessment."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssessmentConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SearchAssessmentStatus(StrEnum):
    """Outcome of the bounded source search probe."""

    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"
    PROBE_FAILED = "probe_failed"
    STALE = "stale"


class SearchExecutor(StrEnum):
    """Transport that successfully applies a keyword search."""

    SPECIFIC_URL = "specific_url"
    SPECIFIC_API = "specific_api"
    GENERIC_GET = "generic_get"
    GENERIC_POST = "generic_post"
    GENERIC_BROWSER = "generic_browser"
    NONE = "none"


class SearchQueryMode(StrEnum):
    """Query syntax accepted by a source search surface."""

    COMBINED_UPPER_OR = "combined_upper_or"
    COMBINED_LOWER_OR = "combined_lower_or"
    EXACT = "exact"
    PER_KEYWORD = "per_keyword"
    NONE = "none"


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)
    confidence: AssessmentConfidence = AssessmentConfidence.MEDIUM
    details: dict[str, Any] = Field(default_factory=dict)


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_family: str = "unknown"
    has_publication_time: bool = False
    has_update_time: bool = False
    has_stable_id: bool = False
    has_stable_url: bool = False
    supports_ordered_head: bool = False
    has_cursor_or_since_filter: bool = False
    has_change_validators: bool = False
    has_page_change_signal: bool = False
    has_rss_or_sitemap_dates: bool = False
    has_embedded_state: bool = False
    known_integration: bool = False


class SearchAssessment(BaseModel):
    """Safe, source-scoped search recipe discovered before ingest.

    The recipe deliberately contains no target roles, cookies, CSRF values or
    credentials. Runtime substitutes the current profile roles into it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SearchAssessmentStatus = SearchAssessmentStatus.UNSUPPORTED
    executor: SearchExecutor = SearchExecutor.NONE
    query_mode: SearchQueryMode = SearchQueryMode.NONE
    parser: str | None = None
    base_url: str | None = None
    action_url: str | None = None
    method: str | None = None
    query_param: str | None = None
    input_selector: str | None = None
    submit_selector: str | None = None
    positive_results: int = 0
    nonsense_results: int = 0
    result_set_changed: bool = False
    query_observed: bool = False
    confidence: AssessmentConfidence = AssessmentConfidence.LOW
    rationale: str = "No verified keyword-search strategy was found."
    strategy_version: str = "search_assessment.v1"


class FreshnessAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confidence: AssessmentConfidence
    can_detect_freshness_without_snapshot: bool = False
    can_filter_since_yesterday: bool = False
    item_level_dates: bool = False
    ordered_by_newest: bool = False
    page_level_change_only: bool = False
    requires_full_snapshot: bool = True
    dates_require_detail_scrape: bool = False
    probe_failed: bool = False
    probe_blocked: bool = False
    rationale: str = Field(min_length=1)


class SourceAssessmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "source_assessment.v3"
    source_id: str
    spec_fingerprint: str = ""
    source_type: str
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    capabilities: SourceCapabilities
    evidence: tuple[SourceEvidence, ...] = ()
    freshness: FreshnessAssessment
    search: SearchAssessment | None = None


class SourceIngestState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    bootstrap_completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "AssessmentConfidence",
    "FreshnessAssessment",
    "SearchAssessment",
    "SearchAssessmentStatus",
    "SearchExecutor",
    "SearchQueryMode",
    "SourceIngestState",
    "SourceAssessmentResult",
    "SourceCapabilities",
    "SourceEvidence",
]
