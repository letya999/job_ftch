"""RawItem -> Job extraction stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    CompensationRange,
    EmploymentType,
    ExtractionRejectionReason,
    JobDraft,
    JobExtractionStatus,
    JobReviewReason,
    JobValidationRejectionReason,
    LanguageCode,
    PostType,
    ProvenanceTrail,
    RawItem,
    Seniority,
    SkillTag,
    SourceKind,
    TriageRejectionReason,
    WorkMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from job_ftch.application.contracts import LLMProvider
    from job_ftch.application.run_budget import AsyncCallBudget

# LLM-classified post types that are not hireable vacancies and must be dropped
# after extraction even if the fast pre-classifier let them through.
_NON_JOB_POST_TYPES = frozenset({PostType.CANDIDATE_SEEKING, PostType.ANNOUNCEMENT, PostType.SPAM})
# What ExtractedJobFields substitutes when the model returns no hiring_intent.
# A declined answer must not read as a passing score.
_NEUTRAL_HIRING_INTENT = 0.5
_TITLE_METADATA_KEYS = ("title", "job_title", "role")
_COMPANY_METADATA_KEYS = ("company", "company_name", "employer", "organization", "org", "service")
_URL_METADATA_KEYS = ("job_url", "canonical_url", "apply_url", "origin_url")
_LOCATION_METADATA_KEYS = ("location", "city", "region", "cities")
_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
# Work modes that the heuristic accepts in any casing. Sourced from Telegram
# and Russian career-site vocabulary.
_WORK_MODE_TOKENS = {
    WorkMode.REMOTE: ("remote", "удален", "удалён", "удалённо", "удаленка"),
    WorkMode.HYBRID: ("hybrid", "гибрид"),
    WorkMode.ONSITE: ("on-site", "onsite", "office", "офис", "в офисе", "в офис"),
}
_VACANCY_STRUCTURE_TOKENS = (
    "обязанности:",
    "требования:",
    "мы предлагаем",
    "условия:",
    "контакты:",
    "чем предстоит заниматься",
    "what you'll do",
    "requirements:",
)


def _preclassified_post_type(item: RawItem) -> PostType:
    raw = item.metadata.get("preclassified_post_type", PostType.UNKNOWN.value)
    try:
        return PostType(str(raw))
    except ValueError:
        return PostType.UNKNOWN


class ExtractedJobFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    description: str | None = None
    # Plain string (not AnyHttpUrl): OpenAI structured-output strict mode
    # (instructor TOOLS_STRICT) rejects JSON Schema "format": "uri", returning
    # HTTP 400 and breaking every extraction. We coerce to AnyHttpUrl after the
    # response via _coerce_url().
    canonical_url: str | None = None
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
    search_relevance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "How well this posting matches the candidate's target roles in the "
            "'### CANDIDATE_TARGET_ROLES' section of the user message. 1.0 = directly "
            "one of those roles; 0.0 = clearly a different role. 0.5 if no roles are provided."
        ),
    )
    hiring_intent: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Probability that this is a concrete job opening (hiring intent). "
            "1.0 = clearly a specific vacancy; 0.1 = general news, digest or "
            "hiring-unrelated announcement."
        ),
    )
    language: LanguageCode = LanguageCode.UNKNOWN
    role_family: str | None = None
    role_track: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    domain: str | None = None
    industry: str | None = None
    project_types: tuple[str, ...] | None = None
    responsibilities: tuple[str, ...] | None = None
    requirements_must: tuple[str, ...] | None = None
    requirements_nice: tuple[str, ...] | None = None
    skills_explicit: tuple[SkillTag, ...] | None = None
    skills_inferred: tuple[SkillTag, ...] | None = None
    tools_stack: tuple[str, ...] | None = None
    benefits: tuple[str, ...] | None = None
    culture_signals: tuple[str, ...] | None = None

    # Plan B extensions
    years_experience: int | None = None
    education: str | None = None
    relocation: bool | None = None
    visa_support: bool | None = None
    domain_knowledge: tuple[str, ...] | None = None
    soft_skills: tuple[str, ...] | None = None
    certifications: tuple[str, ...] | None = None
    leadership_level: str | None = None
    ic_or_manager: str | None = None
    company_type: str | None = None
    team_size_hint: str | None = None
    remote_restrictions: str | None = None

    @field_validator("ai_relevance", "search_relevance", "hiring_intent", mode="before")
    @classmethod
    def _coerce_none_scores(cls, value: object, info: object) -> object:
        if value is not None:
            return value
        field_name = getattr(info, "field_name", "")
        if field_name == "search_relevance":
            return 0.5
        if field_name == "hiring_intent":
            return 0.5
        return 0.0

    @field_validator("compensation", mode="before")
    @classmethod
    def _coerce_empty_compensation(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        min_amount = value.get("min_amount")
        max_amount = value.get("max_amount")
        if min_amount is None and max_amount is None:
            return None
        return value

    @model_validator(mode="after")
    def _coerce_none_arrays_to_empty(self) -> ExtractedJobFields:
        """Treat occasional LLM ``null`` arrays as empty tuples.

        GPT-family tool calls sometimes emit ``null`` for optional collection
        fields even when the rest of the payload is valid. Keeping these fields
        nullable at the schema edge avoids throwing away the whole extraction.
        """
        for field_name in (
            "project_types",
            "responsibilities",
            "requirements_must",
            "requirements_nice",
            "skills_explicit",
            "skills_inferred",
            "tools_stack",
            "benefits",
            "culture_signals",
            "domain_knowledge",
            "soft_skills",
            "certifications",
        ):
            value = getattr(self, field_name)
            if value is None:
                object.__setattr__(self, field_name, ())
        return self


class CoreExtractedJobFields(BaseModel):
    """Cheap first-pass schema: only fields needed before policy routing."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    post_type: PostType = PostType.UNKNOWN
    ai_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    search_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    hiring_intent: float = Field(default=0.5, ge=0.0, le=1.0)
    language: LanguageCode = LanguageCode.UNKNOWN
    role_family: str | None = None
    role_track: str | None = None
    seniority: Seniority = Seniority.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    skills_explicit: tuple[SkillTag, ...] | None = None

    @model_validator(mode="after")
    def normalize_collections(self) -> CoreExtractedJobFields:
        if self.skills_explicit is None:
            object.__setattr__(self, "skills_explicit", ())
        return self


def _metadata_text(item: RawItem, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Some sources (Yandex, Greenhouse) put structured lists in metadata.
        # Use the first non-empty entry as a fallback string.
        if isinstance(value, list) and value:
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, dict):
                    for dk in ("name", "title", "label", "value", "city", "location"):
                        dv = entry.get(dk)
                        if isinstance(dv, str) and dv.strip():
                            return dv.strip()
    return None


_GENERIC_SECTION_HEADINGS = frozenset(
    {
        "описание",
        "обязанности",
        "требования",
        "description",
        "responsibilities",
        "requirements",
    }
)
_METADATA_TITLE_PREFIXES = ("published time:",)


def _collapsed_casefold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_unusable_title(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    collapsed = _collapsed_casefold(text)
    if collapsed in _GENERIC_SECTION_HEADINGS:
        return True
    return any(collapsed.startswith(prefix) for prefix in _METADATA_TITLE_PREFIXES)


def _fallback_title(item: RawItem) -> str | None:
    titled = _metadata_text(item, _TITLE_METADATA_KEYS)
    if titled is not None and not _is_unusable_title(titled):
        return titled
    for line in item.text.splitlines():
        candidate = line.strip(" -")
        if not candidate or _is_unusable_title(candidate):
            continue
        return candidate
    return None


def _fallback_company(item: RawItem) -> str | None:
    # Only trust an explicit company supplied by the source (API monitors like
    # Yandex/Greenhouse/Lever set this). The source_name is NOT a usable company:
    # for aggregator boards (hh, rabota, djinni, dou, hirify) it is the board, and
    # even for single-company career pages it is an opaque geo-prefixed slug
    # ("by_andersen", "kz_kaspi_jobs"). A wrong/slug company is worse than none —
    # the draft is flagged MISSING_COMPANY for review instead.
    return _metadata_text(item, _COMPANY_METADATA_KEYS)


def _fallback_location(item: RawItem) -> str | None:
    """Resolve location from source metadata. No lines[1] fallback (that was the
    company name on Telegram-style postings — bug fixed per three-runs review)."""
    return _metadata_text(item, _LOCATION_METADATA_KEYS)


def _fallback_work_mode(text: str, location: str | None) -> WorkMode | None:
    """Returns the detected work_mode, or None when the chain should
    continue to the next fallback (extracted.work_mode is None/UNKNOWN)."""
    lowered = f"{text}\n{location or ''}".casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return None


def _strip_prompt_scaffnewing(text: str) -> str:
    """Remove extraction-only prompt wrappers accidentally echoed by the LLM."""
    marker = "### JOB_POSTING"
    if marker not in text:
        return text
    _, _, tail = text.partition(marker)
    if ":\n" in tail:
        _, _, tail = tail.partition(":\n")
    return tail.lstrip()


def _has_strong_vacancy_structure(text: str) -> bool:
    lowered = text.casefold()
    hits = sum(1 for token in _VACANCY_STRUCTURE_TOKENS if token in lowered)
    return hits >= 2


# Maps the work_mode tokens we recognise in source metadata (Yandex API,
# Greenhouse, Lever, etc.) to the WorkMode enum. Single source of truth so
# the heuristic and the extraction node fall back consistently.
_WORK_MODE_METADATA_TOKENS: dict[WorkMode, tuple[str, ...]] = {
    WorkMode.REMOTE: (
        "remote",
        "telecommute",  # schema.org JobPosting.jobLocationType
        "удалённо",
        "удаленно",
        "удалён",
        "удален",
        "удаленка",
        "fully remote",
    ),
    WorkMode.HYBRID: ("hybrid", "гибрид", "смешанный"),
    WorkMode.ONSITE: (
        "on-site",
        "onsite",
        "office",
        "офис",
        "в офисе",
        "on site",
    ),
}


_WORK_MODE_METADATA_KEYS = ("work_modes", "job_location_type")


def _fallback_work_mode_from_metadata(metadata: Mapping[str, object]) -> WorkMode:
    """Read work_mode from structured source metadata (Yandex ``work_modes``,
    schema.org ``jobLocationType``, etc.) when the heuristic and text-fallback
    have no answer. Empty/missing values return ``UNKNOWN``.

    ``job_location_type`` carries schema.org's TELECOMMUTE marker, which several
    career sites publish in JSON-LD while stating the mode nowhere in the prose.
    """
    candidates: list[str] = []
    for key in _WORK_MODE_METADATA_KEYS:
        raw = metadata.get(key)
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    candidates.append(entry)
                elif isinstance(entry, dict):
                    for dk in ("name", "title", "label", "value"):
                        dv = entry.get(dk)
                        if isinstance(dv, str):
                            candidates.append(dv)
                            break
        elif isinstance(raw, str):
            candidates.append(raw)
    for candidate in candidates:
        normalized = candidate.casefold().strip()
        for work_mode, tokens in _WORK_MODE_METADATA_TOKENS.items():
            if any(token in normalized or normalized in token for token in tokens):
                return work_mode
    return WorkMode.UNKNOWN


def _coerce_url(value: str | None) -> AnyHttpUrl | None:
    """Validate an LLM-provided URL string, returning None when malformed."""
    if not value:
        return None
    try:
        return _URL_ADAPTER.validate_python(value)
    except ValueError:
        return None


def _fallback_url(item: RawItem) -> AnyHttpUrl | None:
    metadata_url = _metadata_text(item, _URL_METADATA_KEYS)
    if metadata_url is not None:
        return _URL_ADAPTER.validate_python(metadata_url)
    return item.url


class ExtractionNode:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        max_calls: int | None = None,
        budget: AsyncCallBudget | None = None,
        target_roles: tuple[str, ...] = (),
        min_search_relevance: float = 0.0,
        min_hiring_intent: float = 0.0,
        capture_payloads: bool = False,
        scope: str = "full",
    ) -> None:
        self._llm = llm
        self._max_calls = max_calls
        self._budget = budget
        self._target_roles = target_roles
        self._min_search_relevance = min_search_relevance
        self._min_hiring_intent = min_hiring_intent
        # Injected from Settings.tracing_capture_payloads at construction time
        # in application.builder.build_nodes() (per Wave 2.9.4). The flag
        # gates whether raw LLM input/output is attached to OTel spans.
        self._capture_payloads = capture_payloads
        if scope not in {"core", "full"}:
            raise ValueError("Extraction scope must be 'core' or 'full'.")
        self._scope = scope
        self._extraction_mode = "llm_or_structured"
        self._call_count = 0

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "extraction_mode" not in params:
            return
        mode = str(params["extraction_mode"])
        if mode not in {"llm_or_structured", "structured_or_heuristic"}:
            raise ValueError(f"unsupported extraction_mode: {mode}")
        self._extraction_mode = mode

    def _should_override_non_job_post_type(
        self,
        item: RawItem,
        extracted: ExtractedJobFields,
        *,
        title: str | None,
        canonical_url: AnyHttpUrl | None,
    ) -> bool:
        """Keep clear career-site detail pages when the LLM wobbles on post_type.

        Detail pages from career sites are a high-prior source of real vacancies.
        After the stricter post_type drop was added, some obvious vacancies started
        flipping to ``announcement`` despite strong hiring signal. For those pages,
        prefer the source prior when the extraction still produced concrete job
        structure and high hiring intent.
        """
        if item.source_kind is not SourceKind.CAREER_SITE:
            return bool(title) and _has_strong_vacancy_structure(item.text)
        return title is not None or canonical_url is not None or item.url is not None

    async def process(self, item: RawItem | JobDraft) -> JobDraft | None:
        if isinstance(item, JobDraft):
            return item
        structured_fast_path = item.metadata.get("extraction_cost_hint") == "structured"
        if self._extraction_mode == "structured_or_heuristic" and not structured_fast_path:
            return self._heuristic_triage_draft(item)
        if not structured_fast_path and self._budget is not None:
            acquired = await self._budget.try_acquire()
            if not acquired:
                return self._deferred_budget_draft(
                    item, f"LLM call budget of {self._budget.limit} reached for this run."
                )
        elif (
            not structured_fast_path
            and self._max_calls is not None
            and self._call_count >= self._max_calls
        ):
            return self._deferred_budget_draft(
                item, f"LLM call budget of {self._max_calls} reached for this run."
            )
        if not structured_fast_path and self._budget is None:
            self._call_count += 1
        extracted, degraded = await self._extract_fields(item)
        extracted_title = extracted.title
        if extracted_title is not None and (
            self._looks_like_target_roles(extracted_title) or _is_unusable_title(extracted_title)
        ):
            # Guard against the LLM echoing the candidate's target-roles list,
            # a section heading like «Описание», or a Telegram "Published time:"
            # prefix into the title. Fall back to the posting.
            extracted_title = None
        title = extracted_title or _fallback_title(item)
        company = extracted.company or _fallback_company(item)
        description = _strip_prompt_scaffnewing(extracted.description or item.text).strip()
        canonical_url = _coerce_url(extracted.canonical_url) or _fallback_url(item)
        location = extracted.location or _fallback_location(item)
        work_mode = (
            extracted.work_mode
            if extracted.work_mode is not None and extracted.work_mode is not WorkMode.UNKNOWN
            else (
                _fallback_work_mode(description, location)
                or _fallback_work_mode_from_metadata(item.metadata)
            )
        )
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
        resolved_post_type = extracted.post_type
        if degraded and resolved_post_type is PostType.UNKNOWN:
            resolved_post_type = _preclassified_post_type(item)
        # The fast rules classifier (HardFilterNode) runs on the PRE-classified
        # post_type before extraction and can mislabel a candidate/announcement/
        # spam post as a job. The LLM extraction above produces a far more
        # reliable post_type, so drop here when it disagrees — otherwise resumes
        # ("#resume", "open to work") and digests leak through as fake vacancies.
        if not degraded and extracted.post_type in _NON_JOB_POST_TYPES:
            if self._should_override_non_job_post_type(
                item,
                extracted,
                title=title,
                canonical_url=canonical_url,
            ):
                resolved_post_type = PostType.JOB_POSTING
            else:
                raise RawItemDropped(
                    reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                    details=(
                        f"LLM classified post_type={extracted.post_type.value!r} "
                        "(not a job posting)."
                    ),
                    item=item,
                )
        if (
            self._min_search_relevance > 0.0
            and not degraded
            and resolved_post_type is PostType.JOB_POSTING
            and extracted.search_relevance < self._min_search_relevance
        ):
            raise RawItemDropped(
                reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                details=(
                    f"LLM search_relevance={extracted.search_relevance:.2f} below "
                    f"min={self._min_search_relevance:.2f} for target roles."
                ),
                item=item,
            )
        if self._min_hiring_intent > 0.0 and not degraded:
            # UNKNOWN used to slip between both guards: it is not in
            # _NON_JOB_POST_TYPES, so it was never dropped, and this gate only
            # looked at JOB_POSTING, so it was never scored. A chat message
            # ("LLM-инженер, я так понимаю, это не вайбкодер?") classified
            # UNKNOWN therefore reached delivery as a vacancy.
            #
            # When the model cannot name the post kind, the hiring signal has to
            # carry it, and the neutral default is not evidence. An informal post
            # the model reads as actively hiring still passes - that is the case
            # worth keeping, since real vacancies do get posted conversationally.
            uncommitted = resolved_post_type is PostType.UNKNOWN
            threshold = (
                max(self._min_hiring_intent, _NEUTRAL_HIRING_INTENT)
                if uncommitted
                else self._min_hiring_intent
            )
            below = (
                extracted.hiring_intent <= threshold
                if uncommitted
                else extracted.hiring_intent < threshold
            )
            if resolved_post_type in (PostType.JOB_POSTING, PostType.UNKNOWN) and below:
                raise RawItemDropped(
                    reason=JobValidationRejectionReason.JOB_OUT_OF_SCOPE,
                    details=(
                        f"LLM hiring_intent={extracted.hiring_intent:.2f} at or below "
                        f"threshold={threshold:.2f} for post_type={resolved_post_type.value}. "
                        "Detected news/digest/discussion or non-hiring announcement."
                    ),
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
        if "original_posting_text" not in metadata:
            metadata["original_posting_text"] = item.text
        metadata["extraction_backend"] = self._llm.__class__.__name__
        metadata["llm_search_relevance"] = extracted.search_relevance
        metadata["hiring_intent"] = extracted.hiring_intent
        metadata["llm_post_type_raw"] = extracted.post_type.value
        if resolved_post_type is not extracted.post_type:
            metadata["llm_post_type_override"] = resolved_post_type.value
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
            provenance=ProvenanceTrail(extraction=tuple(extraction_steps)),
            metadata=metadata,
            post_type=resolved_post_type,
            ai_relevance=extracted.ai_relevance,
            hiring_intent=extracted.hiring_intent,
            language=extracted.language,
            role_family=extracted.role_family,
            role_track=extracted.role_track,
            seniority=extracted.seniority,
            employment_type=extracted.employment_type,
            domain=extracted.domain,
            industry=extracted.industry,
            project_types=extracted.project_types or (),
            responsibilities=extracted.responsibilities or (),
            requirements_must=extracted.requirements_must or (),
            requirements_nice=extracted.requirements_nice or (),
            skills_explicit=extracted.skills_explicit or (),
            skills_inferred=extracted.skills_inferred or (),
            tools_stack=extracted.tools_stack or (),
            benefits=extracted.benefits or (),
            culture_signals=extracted.culture_signals or (),
            # Plan B extensions
            years_experience=extracted.years_experience,
            education=extracted.education,
            relocation=extracted.relocation,
            visa_support=extracted.visa_support,
            domain_knowledge=extracted.domain_knowledge or (),
            soft_skills=extracted.soft_skills or (),
            certifications=extracted.certifications or (),
            leadership_level=extracted.leadership_level,
            ic_or_manager=extracted.ic_or_manager,
            company_type=extracted.company_type,
            team_size_hint=extracted.team_size_hint,
            remote_restrictions=extracted.remote_restrictions,
        )

    def _deferred_budget_draft(self, item: RawItem, detail: str) -> JobDraft:
        """Preserve an eligible observation for review when LLM budget is spent."""
        metadata = {
            **item.metadata,
            "budget_outcome": "deferred",
            "budget_deferred_reason": detail,
            "original_posting_text": item.metadata.get("original_posting_text", item.text),
        }
        return JobDraft(
            raw_item_id=item.stable_id,
            source_record_id=item.external_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
            source_url=item.url,
            title_raw=_fallback_title(item),
            company_name_raw=_fallback_company(item),
            description_raw=item.text,
            canonical_url=_fallback_url(item),
            fetched_at=item.fetched_at,
            posted_at=item.created_at,
            location_raw=_fallback_location(item),
            work_mode=_fallback_work_mode_from_metadata(item.metadata),
            extraction_status=JobExtractionStatus.PARTIAL,
            post_type=_preclassified_post_type(item),
            review_reasons=(JobReviewReason.PARTIAL_EXTRACTION.value, "budget_deferred"),
            provenance=ProvenanceTrail(extraction=("budget:deferred",)),
            metadata=metadata,
        )

    def _heuristic_triage_draft(self, item: RawItem) -> JobDraft:
        """Create the typed policy payload without spending an extraction call."""
        location = _fallback_location(item)
        work_mode = _fallback_work_mode(item.text, location) or _fallback_work_mode_from_metadata(
            item.metadata
        )
        metadata = {
            **item.metadata,
            "extraction_backend": "heuristic_triage",
            "original_posting_text": item.metadata.get("original_posting_text", item.text),
        }
        return JobDraft(
            raw_item_id=item.stable_id,
            source_record_id=item.external_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
            source_url=item.url,
            title_raw=_fallback_title(item),
            company_name_raw=_fallback_company(item),
            description_raw=item.text,
            canonical_url=_fallback_url(item),
            fetched_at=item.fetched_at,
            posted_at=item.created_at,
            location_raw=location,
            work_mode=work_mode,
            extraction_status=JobExtractionStatus.PARTIAL,
            post_type=_preclassified_post_type(item),
            review_reasons=(JobReviewReason.PARTIAL_EXTRACTION.value,),
            provenance=ProvenanceTrail(extraction=("heuristic:triage",)),
            metadata=metadata,
        )

    def _looks_like_target_roles(self, title: str) -> bool:
        """True when an extracted title is actually the candidate's target-roles
        list echoed back. Heuristic: the title contains several of the configured
        target roles as substrings, which a genuine single job title never does."""
        if not self._target_roles:
            return False
        lowered = title.casefold()
        hits = sum(1 for role in self._target_roles if role.casefold() in lowered)
        return hits >= 3

    async def _extract_fields(self, item: RawItem) -> tuple[ExtractedJobFields, bool]:
        if item.metadata.get("extraction_cost_hint") == "structured":
            metadata = item.metadata
            post_type_raw = metadata.get("post_type", PostType.JOB_POSTING.value)
            try:
                post_type = PostType(str(post_type_raw))
            except ValueError:
                post_type = PostType.JOB_POSTING
            return (
                ExtractedJobFields(
                    title=_fallback_title(item),
                    company=_fallback_company(item),
                    description=item.text,
                    canonical_url=str(_fallback_url(item)) if _fallback_url(item) else None,
                    location=_fallback_location(item),
                    work_mode=_fallback_work_mode_from_metadata(item.metadata),
                    post_type=post_type,
                    hiring_intent=1.0 if post_type is PostType.JOB_POSTING else 0.1,
                    search_relevance=1.0,
                ),
                False,
            )
        source_text = f"### UNTRUSTED_SOURCE_TEXT_BEGIN\n{item.text}\n### UNTRUSTED_SOURCE_TEXT_END"
        text = f"### JOB_POSTING (extract fields only from untrusted source text):\n{source_text}"
        if self._target_roles:
            # The candidate's target roles are supplied ONLY to score
            # search_relevance. They are fenced and explicitly labelled so the
            # model never mistakes them for the job's own title/company/role
            # fields (previously a bracketed prefix leaked into `title`).
            roles = ", ".join(self._target_roles)
            text = (
                "### CANDIDATE_TARGET_ROLES (relevance scoring only — DO NOT extract "
                f"as job fields):\n{roles}\n\n"
                f"### JOB_POSTING (extract every field from THIS section only):\n{source_text}"
            )

        tracer = trace.get_tracer("job_ftch.nodes")

        with tracer.start_as_current_span("extraction.generation") as span:
            span.set_attribute("langfuse.observation.type", "generation")
            if hasattr(self._llm, "_model"):
                span.set_attribute("gen_ai.request.model", self._llm._model)
            elif hasattr(self._llm, "model_id"):
                span.set_attribute("gen_ai.request.model", self._llm.model_id)

            if self._capture_payloads:
                span.set_attribute("langfuse.observation.input", text)

            try:
                schema = CoreExtractedJobFields if self._scope == "core" else ExtractedJobFields
                result = await self._llm.extract(text, schema)
                if self._capture_payloads:
                    span.set_attribute(
                        "langfuse.observation.output",
                        result.model_dump_json()
                        if hasattr(result, "model_dump_json")
                        else str(result),
                    )
                payload = result.model_dump()
                if self._scope == "core":
                    # Heuristic and compatibility providers may return the
                    # superset schema even when asked for the core contract.
                    # Keep only fields explicitly admitted by the cheap path;
                    # optional enrichment fields belong after ACCEPT.
                    payload = {
                        key: value
                        for key, value in payload.items()
                        if key in CoreExtractedJobFields.model_fields
                    }
                return ExtractedJobFields.model_validate(payload), False
            except (TypeError, AttributeError) as exc:
                # Schema/code bug — should not happen in production.
                # Log as error so it surfaces in monitoring.
                import structlog

                structlog.get_logger("job_ftch.extraction").error(
                    "extraction_schema_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    item_id=getattr(item, "stable_id", None),
                )
                span.set_attribute("job_ftch.extraction.error_type", "schema")
                span.set_attribute("job_ftch.extraction.error", str(exc))
                return ExtractedJobFields(), True
            except Exception as exc:
                # LLM/network failure — instructor already retried max_retries
                # times; this is the final fallback.
                import structlog

                structlog.get_logger("job_ftch.extraction").warning(
                    "extraction_llm_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    item_id=getattr(item, "stable_id", None),
                )
                span.set_attribute("job_ftch.extraction.error_type", "llm")
                span.set_attribute("job_ftch.extraction.error", str(exc))
                return ExtractedJobFields(), True


ExtractedJobFields.model_rebuild()
