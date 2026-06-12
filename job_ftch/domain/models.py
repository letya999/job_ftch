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


class LanguageCode(StrEnum):
    RU = "ru"
    EN = "en"
    KK = "kk"
    UZ = "uz"
    UNKNOWN = "unknown"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERN = "intern"
    FREELANCE = "freelance"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"
    STAFF = "staff"
    PRINCIPAL = "principal"
    HEAD = "head"
    DIRECTOR = "director"
    EXECUTIVE = "executive"
    UNKNOWN = "unknown"


class PostType(StrEnum):
    JOB_POSTING = "job_posting"
    CANDIDATE_SEEKING = "candidate_seeking"
    ANNOUNCEMENT = "announcement"
    SPAM = "spam"
    UNKNOWN = "unknown"


class JobExtractionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class MatchDecision(StrEnum):
    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"


class CompensationPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    PROJECT = "project"
    UNKNOWN = "unknown"


class SkillTag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str = Field(min_length=1)
    skill_id: str | None = None
    category: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    aliases: tuple[str, ...] = ()
    source: str | None = None

    @model_validator(mode="after")
    def validate_skill(self) -> SkillTag:
        canonical_name = self.canonical_name.strip()
        if not canonical_name:
            raise ValueError("canonical_name must not be blank.")
        aliases = tuple(alias.strip() for alias in self.aliases if alias.strip())
        object.__setattr__(self, "canonical_name", canonical_name)
        object.__setattr__(self, "aliases", aliases)
        if self.skill_id is not None:
            object.__setattr__(self, "skill_id", self.skill_id.strip() or None)
        if self.category is not None:
            object.__setattr__(self, "category", self.category.strip() or None)
        if self.source is not None:
            object.__setattr__(self, "source", self.source.strip() or None)
        return self


class AuthorityScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str | None = None
    owns_architecture: bool = False
    owns_hiring: bool = False
    owns_delivery: bool = False
    owns_product_scope: bool = False
    direct_reports: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalize(self) -> AuthorityScope:
        if self.level is not None:
            object.__setattr__(self, "level", self.level.strip() or None)
        return self


class CompensationRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=3, max_length=3)
    min_amount: int | None = Field(default=None, ge=0)
    max_amount: int | None = Field(default=None, ge=0)
    period: CompensationPeriod = CompensationPeriod.UNKNOWN
    gross: bool | None = None
    equity: bool | None = None
    bonus: bool | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> CompensationRange:
        currency = self.currency.strip().upper()
        if len(currency) != 3:
            raise ValueError("currency must be a 3-letter code.")
        object.__setattr__(self, "currency", currency)
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


class ProfileMatchScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    hard_pass: bool = True
    vacancy_type_score: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_role_score: float = Field(default=0.0, ge=0.0, le=1.0)
    title_score: float = Field(default=0.0, ge=0.0, le=1.0)
    skills_score: float = Field(default=0.0, ge=0.0, le=1.0)
    domain_score: float = Field(default=0.0, ge=0.0, le=1.0)
    seniority_score: float = Field(default=0.0, ge=0.0, le=1.0)
    region_score: float = Field(default=0.0, ge=0.0, le=1.0)
    salary_score: float = Field(default=0.0, ge=0.0, le=1.0)
    culture_score: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    final_score: float = Field(default=0.0, ge=0.0, le=1.0)
    decision: MatchDecision = MatchDecision.REVIEW
    explanation: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> ProfileMatchScore:
        object.__setattr__(self, "profile_id", self.profile_id.strip())
        object.__setattr__(self, "profile_name", self.profile_name.strip())
        if self.explanation is not None:
            object.__setattr__(self, "explanation", self.explanation.strip() or None)
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


class JobDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str = ""
    raw_item_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    canonical_url: AnyHttpUrl | None = None
    language: LanguageCode = LanguageCode.UNKNOWN
    languages_detected: tuple[LanguageCode, ...] = ()
    title_raw: str | None = None
    company_name_raw: str | None = None
    description_raw: str = Field(min_length=1)
    location_raw: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    compensation: CompensationRange | None = None
    post_type: PostType = PostType.UNKNOWN
    ai_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    role_family: str | None = None
    role_track: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    extraction_status: JobExtractionStatus = JobExtractionStatus.COMPLETE
    authority_scope: AuthorityScope | None = None
    region: str | None = None
    country: str | None = None
    city: str | None = None
    timezone: str | None = None
    domain: str | None = None
    industry: str | None = None
    project_types: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    requirements_must: tuple[str, ...] = ()
    requirements_nice: tuple[str, ...] = ()
    skills_explicit: tuple[SkillTag, ...] = ()
    skills_inferred: tuple[SkillTag, ...] = ()
    tools_stack: tuple[str, ...] = ()
    benefits: tuple[str, ...] = ()
    culture_signals: tuple[str, ...] = ()
    risk_signals: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_draft(self) -> JobDraft:
        for field_name in ("raw_item_id", "source_name", "description_raw"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    raise ValueError(f"{field_name} must not be blank.")
                object.__setattr__(self, field_name, stripped)

        optional_text_fields = (
            "title_raw",
            "company_name_raw",
            "location_raw",
            "role_family",
            "role_track",
            "region",
            "country",
            "city",
            "timezone",
            "domain",
            "industry",
        )
        for field_name in optional_text_fields:
            value = getattr(self, field_name)
            if value is None:
                continue
            stripped = value.strip()
            object.__setattr__(self, field_name, stripped or None)

        object.__setattr__(self, "project_types", _normalized_tuple(self.project_types))
        object.__setattr__(self, "responsibilities", _normalized_tuple(self.responsibilities))
        object.__setattr__(self, "requirements_must", _normalized_tuple(self.requirements_must))
        object.__setattr__(self, "requirements_nice", _normalized_tuple(self.requirements_nice))
        object.__setattr__(self, "tools_stack", _normalized_tuple(self.tools_stack))
        object.__setattr__(self, "benefits", _normalized_tuple(self.benefits))
        object.__setattr__(self, "culture_signals", _normalized_tuple(self.culture_signals))
        object.__setattr__(self, "risk_signals", _normalized_tuple(self.risk_signals))
        normalized_reasons = tuple(
            reason.strip()
            for reason in self.review_reasons
            if isinstance(reason, str) and reason.strip()
        )
        object.__setattr__(self, "review_reasons", normalized_reasons)
        object.__setattr__(
            self,
            "draft_id",
            _stable_hash(
                self.raw_item_id,
                self.title_raw or "",
                self.company_name_raw or "",
                str(self.canonical_url or ""),
            ),
        )
        return self


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = ""
    raw_item_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_name: str = Field(min_length=1)

    # Legacy core fields preserved for compatibility.
    title: str | None = None
    company: str | None = None
    company_canonical: str | None = None
    description: str = Field(min_length=1)
    canonical_url: AnyHttpUrl | None = None
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    compensation: CompensationRange | None = None
    extraction_status: JobExtractionStatus = JobExtractionStatus.COMPLETE
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    post_type: PostType = PostType.UNKNOWN
    ai_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    review_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Rich canonical vacancy model.
    language: LanguageCode = LanguageCode.UNKNOWN
    languages_detected: tuple[LanguageCode, ...] = ()
    title_raw: str | None = None
    title_normalized: str | None = None
    role_family: str | None = None
    role_track: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    authority_scope: AuthorityScope | None = None
    region: str | None = None
    country: str | None = None
    city: str | None = None
    timezone: str | None = None
    domain: str | None = None
    industry: str | None = None
    project_types: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    requirements_must: tuple[str, ...] = ()
    requirements_nice: tuple[str, ...] = ()
    skills_explicit: tuple[SkillTag, ...] = ()
    skills_inferred: tuple[SkillTag, ...] = ()
    tools_stack: tuple[str, ...] = ()
    benefits: tuple[str, ...] = ()
    culture_signals: tuple[str, ...] = ()
    risk_signals: tuple[str, ...] = ()
    profile_scores: tuple[ProfileMatchScore, ...] = ()

    @model_validator(mode="after")
    def validate_job(self) -> Job:
        for field_name in ("raw_item_id", "source_name", "description"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    raise ValueError(f"{field_name} must not be blank.")
                object.__setattr__(self, field_name, stripped)

        optional_text_fields = (
            "title",
            "company",
            "company_canonical",
            "location",
            "title_raw",
            "title_normalized",
            "role_family",
            "role_track",
            "region",
            "country",
            "city",
            "timezone",
            "domain",
            "industry",
        )
        for field_name in optional_text_fields:
            value = getattr(self, field_name)
            if value is None:
                continue
            stripped = value.strip()
            object.__setattr__(self, field_name, stripped or None)

        normalized_reasons = tuple(
            reason.strip()
            for reason in self.review_reasons
            if isinstance(reason, str) and reason.strip()
        )
        object.__setattr__(self, "review_reasons", normalized_reasons)
        object.__setattr__(self, "project_types", _normalized_tuple(self.project_types))
        object.__setattr__(self, "responsibilities", _normalized_tuple(self.responsibilities))
        object.__setattr__(self, "requirements_must", _normalized_tuple(self.requirements_must))
        object.__setattr__(self, "requirements_nice", _normalized_tuple(self.requirements_nice))
        object.__setattr__(self, "tools_stack", _normalized_tuple(self.tools_stack))
        object.__setattr__(self, "benefits", _normalized_tuple(self.benefits))
        object.__setattr__(self, "culture_signals", _normalized_tuple(self.culture_signals))
        object.__setattr__(self, "risk_signals", _normalized_tuple(self.risk_signals))

        if self.title is None and self.title_normalized is not None:
            object.__setattr__(self, "title", self.title_normalized)
        if self.title_raw is None and self.title is not None:
            object.__setattr__(self, "title_raw", self.title)
        if self.title_normalized is None and self.title is not None:
            object.__setattr__(self, "title_normalized", self.title)

        object.__setattr__(
            self,
            "stable_id",
            _stable_hash(
                str(self.canonical_url or ""),
                self.title_normalized or self.title or "",
                self.company_canonical or self.company or "",
                self.raw_item_id,
            ),
        )
        return self


class JobRecord(Job):
    job_id: str = ""
    group_id: str | None = None
    schema_version: str = "job_ftch.job_record.v1"
    description_raw: str | None = None
    description_clean: str | None = None
    company_name_raw: str | None = None
    company_name_normalized: str | None = None
    best_profile_id: str | None = None
    best_score: float | None = Field(default=None, ge=0.0, le=1.0)
    routing_decision: MatchDecision | None = None

    @model_validator(mode="after")
    def validate_record(self) -> JobRecord:
        if self.description_raw is None:
            object.__setattr__(self, "description_raw", self.description)
        if self.description_clean is None:
            object.__setattr__(self, "description_clean", self.description)
        if self.company_name_raw is None:
            object.__setattr__(self, "company_name_raw", self.company)
        if self.company_name_normalized is None:
            object.__setattr__(
                self,
                "company_name_normalized",
                self.company_canonical or self.company,
            )
        if self.best_profile_id is None and self.profile_scores:
            best = max(self.profile_scores, key=lambda score: score.final_score)
            object.__setattr__(self, "best_profile_id", best.profile_id)
            object.__setattr__(self, "best_score", best.final_score)
            if self.routing_decision is None:
                object.__setattr__(self, "routing_decision", best.decision)
        if self.job_id == "":
            object.__setattr__(self, "job_id", self.stable_id)
        return self


def _normalized_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if value.strip())
