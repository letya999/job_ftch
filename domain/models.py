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
