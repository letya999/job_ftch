<<<<<<< HEAD
# domain/models.py
"""Domain models: RawItem and Job."""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """Raw item from source (before processing)."""

    id: str = Field(..., description="Unique item identifier")
    source_type: str = Field(
        ..., description="Source type: telegram_channel, external_remotive, etc."
    )
    source_id: str = Field(..., description="Source-specific ID (URL, channel name, etc.)")
    content: str = Field(..., description="Raw content text")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    fetched_at: datetime = Field(default_factory=datetime.now, description="When item was fetched")

    class Config:
        frozen = True


class Job(BaseModel):
    """Normalized job vacancy."""

    # Core fields
    title: str = Field(..., max_length=200)
    company: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=5000)

    # Skills & experience
    skills: List[str] = Field(default_factory=list)
    experience_years: int = Field(default=0, ge=0, le=50)

    # Compensation
    salary_min: int = Field(default=0, ge=0)
    salary_max: int = Field(default=0, ge=0)

    # Location
    remote: bool = Field(default=False)
    location: str = Field(default="")

    # Source info
    source: str = Field(..., description="Source identifier")
    url: Optional[str] = Field(default=None)

    # Metadata
    raw_content: Optional[str] = Field(default=None)
    found_at: datetime = Field(default_factory=datetime.now)

    class Config:
        frozen = True


class JobBuilder:
    """Builder for Job objects (for easier construction)."""

    def __init__(self):
        self._data = {}

    def with_source(self, source: str) -> "JobBuilder":
        self._data["source"] = source
        return self

    def with_title(self, title: str) -> "JobBuilder":
        self._data["title"] = title
        return self

    def with_company(self, company: str) -> "JobBuilder":
        self._data["company"] = company
        return self

    def with_description(self, description: str) -> "JobBuilder":
        self._data["description"] = description
        return self

    def with_skills(self, skills: List[str]) -> "JobBuilder":
        self._data["skills"] = skills
        return self

    def with_experience(self, years: int) -> "JobBuilder":
        self._data["experience_years"] = years
        return self

    def with_salary(self, min_salary: int, max_salary: int = 0) -> "JobBuilder":
        self._data["salary_min"] = min_salary
        self._data["salary_max"] = max_salary
        return self

    def with_remote(self, remote: bool) -> "JobBuilder":
        self._data["remote"] = remote
        return self

    def with_location(self, location: str) -> "JobBuilder":
        self._data["location"] = location
        return self

    def with_url(self, url: str) -> "JobBuilder":
        self._data["url"] = url
        return self

    def with_raw_content(self, content: str) -> "JobBuilder":
        self._data["raw_content"] = content
        return self

    def with_metadata(self, metadata: Dict[str, Any]) -> "JobBuilder":
        # Extract common fields from metadata
        if "title" in metadata:
            self._data["title"] = metadata["title"]
        if "company" in metadata:
            self._data["company"] = metadata["company"]
        if "url" in metadata:
            self._data["url"] = metadata["url"]
        if "salary_min" in metadata:
            self._data["salary_min"] = metadata["salary_min"]
        if "salary_max" in metadata:
            self._data["salary_max"] = metadata["salary_max"]
        if "remote" in metadata:
            self._data["remote"] = metadata["remote"]
        if "location" in metadata:
            self._data["location"] = metadata["location"]
        if "experience_years" in metadata:
            self._data["experience_years"] = metadata["experience_years"]
        if "skills" in metadata:
            self._data["skills"] = metadata["skills"]
        return self

    def build(self) -> Job:
        """Build Job instance with defaults."""
        required = ["source", "title", "company"]
        for field in required:
            if field not in self._data:
                self._data[field] = "unknown"

        return Job(**self._data)
=======
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
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    description: str = Field(min_length=1)
    canonical_url: AnyHttpUrl | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    compensation: CompensationRange | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_job(self) -> Job:
        for field_name in ("raw_item_id", "source_name", "title", "company", "description"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    raise ValueError(f"{field_name} must not be blank.")
                object.__setattr__(self, field_name, stripped)
        if self.location is not None:
            stripped_location = self.location.strip()
            object.__setattr__(self, "location", stripped_location or None)
        object.__setattr__(
            self,
            "stable_id",
            _stable_hash(
                str(self.canonical_url or ""),
                self.title,
                self.company,
                self.raw_item_id,
            ),
        )
        return self
>>>>>>> upstream/dev
