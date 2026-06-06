"""Pure domain models and invariants for pipeline payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


def _stable_hash(*parts: str) -> str:
    normalized = "|".join(part.strip().lower() for part in parts if part.strip())
    return sha256(normalized.encode("utf-8")).hexdigest()


class SourceKind(StrEnum):
    DEBUG = "debug"
    TELEGRAM_CHANNEL = "telegram_channel"
    TELEGRAM_GROUP = "telegram_group"
    TELEGRAM_COMMENT = "telegram_comment"
    CAREER_SITE = "career_site"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class JobExtractionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class CompensationRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=3, max_length=3)
    min_amount: int | None = Field(default=None, ge=0)
    max_amount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> CompensationRange:
        if self.min_amount is None and self.max_amount is None:
            msg = "At least one compensation bound must be set."
            raise ValueError(msg)
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            msg = "Compensation min_amount must be <= max_amount."
            raise ValueError(msg)
        return self


class RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = ""
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    external_id: str | None = Field(default=None, min_length=1)
    url: AnyHttpUrl | None = None
    text: str = Field(min_length=1)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> RawItem:
        source_name = self.source_name.strip()
        text = self.text.strip()
        external_id = self.external_id.strip() if self.external_id is not None else None
        if not source_name:
            raise ValueError("source_name must not be blank.")
        if not text:
            raise ValueError("text must not be blank.")
        if not external_id and self.url is None:
            raise ValueError("RawItem requires external_id or url.")
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "external_id", external_id)
        object.__setattr__(
            self,
            "stable_id",
            _stable_hash(
                str(self.source_kind),
                self.source_name,
                self.external_id or "",
                str(self.url or ""),
            ),
        )
        return self


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = ""
    raw_item_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    title: str | None = None
    company: str | None = None
    description: str = Field(min_length=1)
    canonical_url: AnyHttpUrl | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    compensation: CompensationRange | None = None
    extraction_status: JobExtractionStatus = JobExtractionStatus.COMPLETE
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    review_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_job(self) -> Job:
        for field_name in ("raw_item_id", "source_name", "description"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    raise ValueError(f"{field_name} must not be blank.")
                object.__setattr__(self, field_name, stripped)
        for field_name in ("title", "company"):
            value = getattr(self, field_name)
            if value is None:
                continue
            stripped = value.strip()
            object.__setattr__(self, field_name, stripped or None)
        if self.location is not None:
            stripped_location = self.location.strip()
            object.__setattr__(self, "location", stripped_location or None)
        normalized_reasons = tuple(
            reason.strip()
            for reason in self.review_reasons
            if isinstance(reason, str) and reason.strip()
        )
        object.__setattr__(self, "review_reasons", normalized_reasons)
        object.__setattr__(
            self,
            "stable_id",
            _stable_hash(
                str(self.canonical_url or ""),
                self.title or "",
                self.company or "",
                self.raw_item_id,
            ),
        )
        return self
