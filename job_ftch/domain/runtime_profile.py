"""Runtime-managed candidate profile records."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from job_ftch.domain.candidate import CandidateProfile


class ManagedCandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    profile: CandidateProfile
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


ManagedCandidateProfile.model_rebuild(
    _types_namespace={
        "CandidateProfile": import_module("job_ftch.domain.candidate").CandidateProfile
    }
)
