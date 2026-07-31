"""Second-pass enrichment for items that already passed routing policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from job_ftch.domain import (
    EmploymentType,
    JobExtractionStatus,
    JobRecord,
    JobReviewReason,
    LanguageCode,
    MatchDecision,
    RawItem,
    Seniority,
    WorkMode,
)
from job_ftch.nodes.extraction import ExtractionNode, _fallback_work_mode_from_metadata

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider
    from job_ftch.application.run_budget import AsyncCallBudget

# Review reasons owned by extraction completeness. Triage stamps them before
# this node runs, so a successful second pass must re-derive them instead of
# leaving stale flags on a record whose fields are now populated.
_EXTRACTION_REVIEW_REASONS = frozenset(
    {
        JobReviewReason.PARTIAL_EXTRACTION.value,
        JobReviewReason.MISSING_TITLE.value,
        JobReviewReason.MISSING_COMPANY.value,
        JobReviewReason.MISSING_LOCATION.value,
    }
)


def _first_metadata_location(metadata: dict[str, object]) -> str | None:
    """Recover a location the acquisition layer already parsed.

    Site parsers write `metadata['locations']`, but nothing projected it onto
    the canonical `location` field, so every record reached delivery with an
    empty location while the data sat in metadata.
    """
    raw = metadata.get("locations")
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    single = metadata.get("location")
    if isinstance(single, str) and single.strip():
        return single.strip()
    return None


# Fragments that mean the value describes an office or a transit stop rather
# than naming a settlement: "офис рядом с м. Кутузовская" is where the desk is,
# not where the job is.
_NON_PLACE_MARKERS = ("офис", "office", "метро", "станци", "м. ")


def _is_unusable_location(value: str | None) -> bool:
    """True when a location cannot stand on its own as a place on the card.

    Covers the two ways extraction fails here: a bare country code ("RU", "RФ")
    and an office/transit description picked out of a benefits list.
    """
    if not value or not value.strip():
        return True
    text = value.strip()
    if len(text) <= 3:
        return True
    lowered = text.casefold()
    return any(marker in lowered for marker in _NON_PLACE_MARKERS)


def _metadata_skills(metadata: dict[str, object]) -> tuple[str, ...]:
    """Technology tags an API-backed parser already collected.

    Sites that publish a tag list (hirify, Yandex) hand it over as structured
    data. Nothing consumed it, and switching hirify from scraping the rendered
    chips to reading its API removed the keyword list the LLM had been picking
    tools out of - tools_stack coverage on that source fell to zero even though
    the tags were sitting in metadata all along.
    """
    raw = metadata.get("skills")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            value = entry.strip()
            if value.casefold() not in {s.casefold() for s in seen}:
                seen.append(value)
    return tuple(seen[:12])


def _metadata_language(metadata: dict[str, object]) -> LanguageCode | None:
    """Language detected upstream by LanguageDetectionNode/LanguageContextNode."""
    raw = metadata.get("detected_language")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        language = LanguageCode(raw.strip().lower())
    except ValueError:
        return None
    return language if language is not LanguageCode.UNKNOWN else None


class FullExtractionNode:
    """Enrich ACCEPT/REVIEW records without allowing enrichment to reroute them."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        budget: AsyncCallBudget | None = None,
        max_calls: int | None = None,
        target_roles: tuple[str, ...] = (),
        capture_payloads: bool = False,
    ) -> None:
        self._budget = budget
        self._max_calls = max_calls
        self._call_count = 0
        self._extractor = ExtractionNode(
            llm,
            budget=budget,
            max_calls=max_calls,
            target_roles=target_roles,
            capture_payloads=capture_payloads,
            scope="full",
        )

    async def process(self, job: JobRecord) -> JobRecord:
        if (job.metadata or {}).get("work_state") == "deferred":
            return job
        if job.routing_decision not in {MatchDecision.ACCEPT, MatchDecision.REVIEW}:
            return job
        if self._budget is not None:
            if not await self._budget.try_acquire():
                return self._deferred(job, "full extraction budget exhausted")
        elif self._max_calls is not None and self._call_count >= self._max_calls:
            return self._deferred(job, "full extraction budget exhausted")
        if self._budget is None:
            self._call_count += 1
        raw = RawItem(
            stable_id=job.raw_item_id,
            external_id=job.source_record_id or job.raw_item_id,
            source_kind=job.source_kind,
            source_name=job.source_name,
            url=job.source_url,
            text=str(
                job.metadata.get("original_posting_text") or job.description_raw or job.description
            ),
            fetched_at=job.fetched_at or datetime.now(UTC),
            created_at=job.posted_at,
            metadata=dict(job.metadata),
        )
        extracted, degraded = await self._extractor._extract_fields(raw)
        if degraded:
            reasons = tuple(dict.fromkeys((*job.review_reasons, "full_extraction_deferred")))
            return job.model_copy(update={"review_reasons": reasons})
        metadata = {
            **job.metadata,
            "full_extraction_backend": self._extractor._llm.__class__.__name__,
        }

        # Identity-shaped fields the delivery card actually renders. For title
        # and company the LLM wins: metadata often holds listing-page noise.
        title = extracted.title or job.title
        company = extracted.company or job.company

        # Location: metadata is consulted only when the extracted value cannot
        # stand as a place. Neither source is reliably better - the LLM lifted
        # "офис рядом с м. Кутузовская" out of a Sberbank benefits list and
        # answered a bare "RU" on a habr posting, while site metadata sometimes
        # carries the search scope rather than the job's own location and once
        # put a German town in Moscow. So a usable extracted place is kept, and
        # metadata only rescues the cases that are not places at all.
        location = extracted.location or job.location
        if _is_unusable_location(location):
            location = _first_metadata_location(job.metadata) or location

        # Same reasoning for work mode: several sites publish schema.org's
        # TELECOMMUTE marker in JSON-LD while never stating the mode in prose,
        # so a posting that is plainly remote reached the card as "unknown".
        work_mode = extracted.work_mode
        if work_mode is None or work_mode is WorkMode.UNKNOWN:
            work_mode = job.work_mode
        if work_mode is WorkMode.UNKNOWN:
            work_mode = _fallback_work_mode_from_metadata(job.metadata)
        language = job.language
        if extracted.language is not LanguageCode.UNKNOWN:
            language = extracted.language
        elif language is LanguageCode.UNKNOWN:
            language = _metadata_language(job.metadata) or language

        # Completeness policy mirrors ExtractionNode: only title/company gate
        # the status; a missing location is a review reason, not a partial.
        extraction_status = (
            JobExtractionStatus.PARTIAL
            if (title is None or company is None)
            else JobExtractionStatus.COMPLETE
        )
        review_reasons = [
            reason for reason in job.review_reasons if reason not in _EXTRACTION_REVIEW_REASONS
        ]
        if title is None:
            review_reasons.append(JobReviewReason.MISSING_TITLE.value)
        if company is None:
            review_reasons.append(JobReviewReason.MISSING_COMPANY.value)
        if location is None:
            review_reasons.append(JobReviewReason.MISSING_LOCATION.value)
        if extraction_status is JobExtractionStatus.PARTIAL:
            review_reasons.insert(0, JobReviewReason.PARTIAL_EXTRACTION.value)

        return job.model_copy(
            update={
                "title": title,
                "company": company,
                "location": location,
                "language": language,
                "work_mode": work_mode,
                "seniority": extracted.seniority
                if extracted.seniority is not Seniority.UNKNOWN
                else job.seniority,
                "employment_type": extracted.employment_type
                if extracted.employment_type is not EmploymentType.UNKNOWN
                else job.employment_type,
                "role_family": extracted.role_family or job.role_family,
                "role_track": extracted.role_track or job.role_track,
                "domain": extracted.domain or job.domain,
                "industry": extracted.industry or job.industry,
                "review_reasons": tuple(dict.fromkeys(review_reasons)),
                "compensation": extracted.compensation or job.compensation,
                "project_types": extracted.project_types or job.project_types,
                "responsibilities": extracted.responsibilities or job.responsibilities,
                "requirements_must": extracted.requirements_must or job.requirements_must,
                "requirements_nice": extracted.requirements_nice or job.requirements_nice,
                "skills_explicit": extracted.skills_explicit or job.skills_explicit,
                "skills_inferred": extracted.skills_inferred or job.skills_inferred,
                "tools_stack": (
                    extracted.tools_stack or job.tools_stack or _metadata_skills(job.metadata)
                ),
                "benefits": extracted.benefits or job.benefits,
                "culture_signals": extracted.culture_signals or job.culture_signals,
                "domain_knowledge": extracted.domain_knowledge or job.domain_knowledge,
                "soft_skills": extracted.soft_skills or job.soft_skills,
                "certifications": extracted.certifications or job.certifications,
                "years_experience": extracted.years_experience or job.years_experience,
                "education": extracted.education or job.education,
                "relocation": extracted.relocation
                if extracted.relocation is not None
                else job.relocation,
                "visa_support": extracted.visa_support
                if extracted.visa_support is not None
                else job.visa_support,
                "leadership_level": extracted.leadership_level or job.leadership_level,
                "ic_or_manager": extracted.ic_or_manager or job.ic_or_manager,
                "company_type": extracted.company_type or job.company_type,
                "team_size_hint": extracted.team_size_hint or job.team_size_hint,
                "remote_restrictions": extracted.remote_restrictions or job.remote_restrictions,
                "extraction_status": extraction_status,
                "metadata": metadata,
            }
        )

    @staticmethod
    def _deferred(job: JobRecord, detail: str) -> JobRecord:
        reasons = tuple(dict.fromkeys((*job.review_reasons, "full_extraction_deferred")))
        return job.model_copy(
            update={
                "review_reasons": reasons,
                "metadata": {
                    **job.metadata,
                    "full_extraction_outcome": "deferred",
                    "full_extraction_deferred_reason": detail,
                },
            }
        )
