from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_ftch.domain.models import EmploymentType, LanguageCode, Seniority, SkillTag, SourceKind


class ProfileWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: float = Field(default=0.2, ge=0.0, le=1.0)
    semantic_role: float = Field(default=0.15, ge=0.0, le=1.0)
    skills: float = Field(default=0.2, ge=0.0, le=1.0)
    domain: float = Field(default=0.1, ge=0.0, le=1.0)
    seniority: float = Field(default=0.1, ge=0.0, le=1.0)
    region: float = Field(default=0.1, ge=0.0, le=1.0)
    salary: float = Field(default=0.03, ge=0.0, le=1.0)
    culture: float = Field(default=0.02, ge=0.0, le=1.0)
    vector: float = Field(default=0.1, ge=0.0, le=1.0)


class CompensationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=3, max_length=3)
    min_amount: int | None = Field(default=None, ge=0)
    preferred_amount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalize(self) -> CompensationExpectation:
        object.__setattr__(self, "currency", self.currency.strip().upper())
        return self


class SearchProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = "default"
    name: str = "default"
    profile_description: str | None = None
    target_roles: tuple[str, ...] = ()
    target_domains: tuple[str, ...] = ()
    target_industries: tuple[str, ...] = ()
    project_types: tuple[str, ...] = ()
    hard_requirements: tuple[str, ...] = ()
    soft_preferences: tuple[str, ...] = ()
    anti_preferences: tuple[str, ...] = ()
    blocked_companies: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    preferred_regions: tuple[str, ...] = ()
    preferred_countries: tuple[str, ...] = ()
    preferred_cities: tuple[str, ...] = ()
    allowed_languages: tuple[LanguageCode, ...] = ()
    allowed_source_kinds: tuple[SourceKind, ...] = ()
    employment_types: tuple[EmploymentType, ...] = ()
    seniority_min: Seniority = Seniority.UNKNOWN
    seniority_max: Seniority = Seniority.UNKNOWN
    salary_expectation: CompensationExpectation | None = None
    required_skills: tuple[SkillTag, ...] = ()
    preferred_skills: tuple[SkillTag, ...] = ()
    culture_preferences: tuple[str, ...] = ()
    positive_example_texts: tuple[str, ...] = ()
    negative_example_texts: tuple[str, ...] = ()
    positive_job_example_texts: tuple[str, ...] = ()
    negative_job_example_texts: tuple[str, ...] = ()
    embedding_vector: tuple[float, ...] | None = None
    negative_embedding_vectors: tuple[tuple[float, ...], ...] = ()
    relevance_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    weights: ProfileWeights = Field(default_factory=ProfileWeights)

    @field_validator("required_skills", "preferred_skills", mode="before")
    @classmethod
    def _coerce_skill_tags(cls, value: object) -> object:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[SkillTag | object] = []
        for item in value:
            if isinstance(item, SkillTag):
                normalized.append(item)
            elif isinstance(item, str):
                normalized.append(SkillTag(canonical_name=item, source="profile_catalog"))
            else:
                normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def normalize(self) -> SearchProfile:
        object.__setattr__(self, "profile_id", self.profile_id.strip() or "default")
        object.__setattr__(self, "name", self.name.strip() or self.profile_id)
        if self.profile_description is not None:
            object.__setattr__(
                self, "profile_description", self.profile_description.strip() or None
            )
        for field_name in (
            "target_roles",
            "target_domains",
            "target_industries",
            "project_types",
            "hard_requirements",
            "soft_preferences",
            "anti_preferences",
            "blocked_companies",
            "blocked_domains",
            "preferred_regions",
            "preferred_countries",
            "preferred_cities",
            "culture_preferences",
            "positive_example_texts",
            "negative_example_texts",
            "positive_job_example_texts",
            "negative_job_example_texts",
        ):
            values = getattr(self, field_name)
            normalized = tuple(v.strip() for v in values if v.strip())
            object.__setattr__(self, field_name, normalized)
        return self

    @property
    def languages_of_interest(self) -> tuple[LanguageCode, ...]:
        """Backward-compatible alias for newer tests/adapters."""
        return self.allowed_languages


class ProfileCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_name: str = "default"
    profiles: tuple[SearchProfile, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def normalize(self) -> ProfileCatalog:
        object.__setattr__(self, "catalog_name", self.catalog_name.strip() or "default")
        return self
