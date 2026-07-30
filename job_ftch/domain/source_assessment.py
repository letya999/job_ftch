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

    schema_version: str = "source_assessment.v2"
    source_id: str
    source_type: str
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    capabilities: SourceCapabilities
    evidence: tuple[SourceEvidence, ...] = ()
    freshness: FreshnessAssessment


class SourceIngestState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    bootstrap_completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "AssessmentConfidence",
    "FreshnessAssessment",
    "SourceIngestState",
    "SourceAssessmentResult",
    "SourceCapabilities",
    "SourceEvidence",
]
