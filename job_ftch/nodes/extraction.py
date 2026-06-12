"""RawItem -> Job extraction stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    CompensationRange,
    EmploymentType,
    ExtractionRejectionReason,
    JobDraft,
    JobExtractionStatus,
    JobReviewReason,
    LanguageCode,
    PostType,
    RawItem,
    Seniority,
    SkillTag,
    SourceKind,
    WorkMode,
)

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider

_TITLE_METADATA_KEYS = ("title", "job_title", "role")
_COMPANY_METADATA_KEYS = ("company", "company_name", "employer", "organization", "org")
_URL_METADATA_KEYS = ("job_url", "canonical_url", "apply_url", "origin_url")
_LOCATION_METADATA_KEYS = ("location", "city", "region")
_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class ExtractedJobFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    description: str | None = None
    canonical_url: AnyHttpUrl | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    compensation: CompensationRange | None = None
    post_type: PostType = PostType.UNKNOWN
    ai_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0.0 = not an AI/ML role. 1.0 = clearly AI/ML role.",
    )
    language: LanguageCode = LanguageCode.UNKNOWN
    role_family: str | None = None
    role_track: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
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

    # Plan B extensions
    years_experience: int | None = None
    education: str | None = None
    relocation: bool | None = None
    visa_support: bool | None = None
    domain_knowledge: tuple[str, ...] = ()
    soft_skills: tuple[str, ...] = ()
    certifications: tuple[str, ...] = ()
    leadership_level: str | None = None
    ic_or_manager: str | None = None
    company_type: str | None = None
    team_size_hint: str | None = None
    remote_restrictions: str | None = None


def _metadata_text(item: RawItem, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fallback_title(item: RawItem) -> str | None:
    titled = _metadata_text(item, _TITLE_METADATA_KEYS)
    if titled is not None:
        return titled
    first_line = next((line.strip(" -") for line in item.text.splitlines() if line.strip()), "")
    return first_line or None


def _fallback_company(item: RawItem) -> str | None:
    company = _metadata_text(item, _COMPANY_METADATA_KEYS)
    if company is not None:
        return company
    if item.source_kind is SourceKind.CAREER_SITE:
        return item.source_name
    return None


def _fallback_location(item: RawItem) -> str | None:
    location = _metadata_text(item, _LOCATION_METADATA_KEYS)
    if location is not None:
        return location
    lines = [line.strip() for line in item.text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines[1]
    return None


def _fallback_work_mode(text: str, location: str | None) -> WorkMode:
    lowered = f"{text}\n{location or ''}".casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def _fallback_url(item: RawItem) -> AnyHttpUrl | None:
    metadata_url = _metadata_text(item, _URL_METADATA_KEYS)
    if metadata_url is not None:
        return _URL_ADAPTER.validate_python(metadata_url)
    return item.url


class ExtractionNode:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def process(self, item: RawItem) -> JobDraft | None:
        extracted, degraded = await self._extract_fields(item)
        title = extracted.title or _fallback_title(item)
        company = extracted.company or _fallback_company(item)
        description = (extracted.description or item.text).strip()
        canonical_url = extracted.canonical_url or _fallback_url(item)
        location = extracted.location or _fallback_location(item)
        work_mode = extracted.work_mode or _fallback_work_mode(description, location)
        if not description:
            raise RawItemDropped(
                reason=ExtractionRejectionReason.EXTRACTION_EMPTY,
                details="Extraction produced an empty job description.",
                item=item,
            )
        if title is None and company is None and canonical_url is None:
            raise RawItemDropped(
                reason=ExtractionRejectionReason.EXTRACTION_FAILED,
                details="Extraction did not produce enough structured job signal.",
                item=item,
            )
        review_reasons: list[str] = []
        extraction_status = (
            JobExtractionStatus.PARTIAL if degraded else JobExtractionStatus.COMPLETE
        )
        if title is None:
            review_reasons.append(JobReviewReason.MISSING_TITLE.value)
            extraction_status = JobExtractionStatus.PARTIAL
        if company is None:
            review_reasons.append(JobReviewReason.MISSING_COMPANY.value)
            extraction_status = JobExtractionStatus.PARTIAL
        if location is None:
            review_reasons.append(JobReviewReason.MISSING_LOCATION.value)
        if extraction_status is JobExtractionStatus.PARTIAL:
            review_reasons.insert(0, JobReviewReason.PARTIAL_EXTRACTION.value)
        metadata = dict(item.metadata)
        metadata["extraction_backend"] = self._llm.__class__.__name__
        extraction_steps = [f"llm:{self._llm.__class__.__name__}"]
        if degraded:
            extraction_steps.append("fallback:degraded_extraction")
        if title is None:
            extraction_steps.append("fallback:title")
        if company is None:
            extraction_steps.append("fallback:company")
        if canonical_url is None:
            extraction_steps.append("fallback:canonical_url")
        if location is None:
            extraction_steps.append("fallback:location")
        return JobDraft(
            raw_item_id=item.stable_id,
            source_record_id=item.external_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
            source_url=item.url,
            title_raw=title,
            company_name_raw=company,
            description_raw=description,
            canonical_url=canonical_url,
            fetched_at=item.fetched_at,
            posted_at=item.created_at,
            location_raw=location,
            work_mode=work_mode,
            compensation=extracted.compensation,
            extraction_status=extraction_status,
            review_reasons=tuple(review_reasons),
            provenance={"extraction": tuple(extraction_steps)},
            metadata=metadata,
            post_type=extracted.post_type,
            ai_relevance=extracted.ai_relevance,
            language=extracted.language,
            role_family=extracted.role_family,
            role_track=extracted.role_track,
            seniority=extracted.seniority,
            employment_type=extracted.employment_type,
            domain=extracted.domain,
            industry=extracted.industry,
            project_types=extracted.project_types,
            responsibilities=extracted.responsibilities,
            requirements_must=extracted.requirements_must,
            requirements_nice=extracted.requirements_nice,
            skills_explicit=extracted.skills_explicit,
            skills_inferred=extracted.skills_inferred,
            tools_stack=extracted.tools_stack,
            benefits=extracted.benefits,
            culture_signals=extracted.culture_signals,
            # Plan B extensions
            years_experience=extracted.years_experience,
            education=extracted.education,
            relocation=extracted.relocation,
            visa_support=extracted.visa_support,
            domain_knowledge=extracted.domain_knowledge,
            soft_skills=extracted.soft_skills,
            certifications=extracted.certifications,
            leadership_level=extracted.leadership_level,
            ic_or_manager=extracted.ic_or_manager,
            company_type=extracted.company_type,
            team_size_hint=extracted.team_size_hint,
            remote_restrictions=extracted.remote_restrictions,
        )

    async def _extract_fields(self, item: RawItem) -> tuple[ExtractedJobFields, bool]:
        try:
            return await self._llm.extract(item.text, ExtractedJobFields), False
        except Exception:
            return ExtractedJobFields(), True


ExtractedJobFields.model_rebuild()
