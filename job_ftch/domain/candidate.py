from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_ftch.domain.models import LanguageCode, SkillTag  # noqa: TC001
from job_ftch.domain.profile import SearchProfile  # noqa: TC001


class CandidateIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    display_name: str | None = None
    languages: tuple[LanguageCode, ...] = ()
    regions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def normalize(self) -> CandidateIdentity:
        object.__setattr__(self, "candidate_id", self.candidate_id.strip())
        if self.display_name is not None:
            object.__setattr__(self, "display_name", self.display_name.strip() or None)
        object.__setattr__(self, "regions", tuple(r.strip() for r in self.regions if r.strip()))
        return self


class CandidateResumeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = None
    skills: tuple[SkillTag, ...] = ()
    target_roles: tuple[str, ...] = ()
    seniority_hint: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> CandidateResumeSnapshot:
        if self.summary is not None:
            object.__setattr__(self, "summary", self.summary.strip() or None)
        object.__setattr__(
            self,
            "target_roles",
            tuple(role.strip() for role in self.target_roles if role.strip()),
        )
        if self.seniority_hint is not None:
            object.__setattr__(self, "seniority_hint", self.seniority_hint.strip() or None)
        return self


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: CandidateIdentity
    resume: CandidateResumeSnapshot | None = None
    search_profiles: tuple[SearchProfile, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def normalize(self) -> CandidateProfile:
        if not self.search_profiles:
            raise ValueError("CandidateProfile must contain at least one search profile.")
        return self
