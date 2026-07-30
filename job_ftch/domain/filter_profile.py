from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from job_ftch.domain.models import EmploymentType, LanguageCode, Seniority, SkillTag, SourceKind
from job_ftch.domain.profile import ProfileCatalog, ProfileWeights, SearchProfile


class FilterProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "default"
    profile_description: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    target_domains: list[str] = Field(default_factory=list)
    target_industries: list[str] = Field(default_factory=list)
    project_types: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    positive_relevance_keywords: list[str] = Field(default_factory=list)
    negative_relevance_keywords: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    blocked_companies: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    preferred_regions: list[str] = Field(default_factory=list)
    preferred_countries: list[str] = Field(default_factory=list)
    preferred_cities: list[str] = Field(default_factory=list)
    culture_preferences: list[str] = Field(default_factory=list)
    allowed_languages: list[LanguageCode] = Field(default_factory=list)
    allowed_source_kinds: list[SourceKind] | None = None
    employment_types: list[EmploymentType] = Field(default_factory=list)
    seniority_min: Seniority = Seniority.UNKNOWN
    seniority_max: Seniority = Seniority.UNKNOWN
    min_text_tokens: int = Field(default=3, ge=1)
    min_text_chars: int = Field(default=18, ge=1)
    relevance_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    candidate_signal_patterns: list[str] = Field(
        default_factory=lambda: [
            "#candidate",
            "#резюме",
            "#ищуработу",
            "#opentowork",
            "about me:",
            "looking for opportunities",
            "open to work",
            "hi, i'm",
            "привет, меня зовут",
            "ищу работу",
            "в поиске работы",
        ]
    )
    spam_signal_patterns: list[str] = Field(
        default_factory=lambda: [
            r"\d{3,}\.\d{2}\s*odds",
            "скачать музыку",
            "скачать песню",
            r"нажми.{0,20}скачать",
            "casino",
            "betting",
            "букмекер",
        ]
    )
    weights: ProfileWeights = Field(default_factory=ProfileWeights)

    def to_search_profile(self) -> SearchProfile:
        required_skills = tuple(
            SkillTag(canonical_name=skill, source="filter_profile")
            for skill in self.required_skills
            if skill.strip()
        )
        preferred_skills = tuple(
            SkillTag(canonical_name=skill, source="filter_profile")
            for skill in self.preferred_skills
            if skill.strip()
        )
        return SearchProfile(
            profile_id=self.name,
            name=self.name,
            profile_description=self.profile_description,
            target_roles=tuple(self.target_roles or self.positive_relevance_keywords),
            target_domains=tuple(self.target_domains),
            target_industries=tuple(self.target_industries),
            project_types=tuple(self.project_types),
            hard_requirements=tuple(self.required_keywords),
            soft_preferences=tuple(self.positive_relevance_keywords),
            anti_preferences=tuple(self.negative_relevance_keywords),
            blocked_companies=tuple(self.blocked_companies),
            blocked_domains=tuple(self.blocked_domains),
            preferred_regions=tuple(self.preferred_regions),
            preferred_countries=tuple(self.preferred_countries),
            preferred_cities=tuple(self.preferred_cities),
            allowed_languages=tuple(self.allowed_languages),
            allowed_source_kinds=(
                tuple(self.allowed_source_kinds) if self.allowed_source_kinds is not None else ()
            ),
            employment_types=tuple(self.employment_types),
            seniority_min=self.seniority_min,
            seniority_max=self.seniority_max,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            culture_preferences=tuple(self.culture_preferences),
            relevance_threshold=self.relevance_threshold,
            weights=self.weights,
        )

    def to_catalog(self) -> ProfileCatalog:
        return ProfileCatalog(catalog_name=self.name, profiles=(self.to_search_profile(),))

    @classmethod
    def default(cls) -> FilterProfile:
        return cls(
            name="default",
            profile_description=None,
            target_roles=[],
            target_domains=[],
            required_keywords=[],
            exclude_keywords=[
                "subscribe",
                "follow us",
                "webinar",
                "meetup",
                "conference",
                "course",
                "training",
                "newsletter",
                "digest",
                "news",
                "podcast",
                "like and share",
            ],
            positive_relevance_keywords=[],
            negative_relevance_keywords=[],
            preferred_skills=[],
            allowed_languages=[LanguageCode.RU, LanguageCode.EN, LanguageCode.UNKNOWN],
            allowed_source_kinds=None,
            min_text_tokens=3,
            min_text_chars=18,
            relevance_threshold=0.0,
        )
