"""Normalization stages for structured Job fields."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from job_ftch.application.compensation import (
    normalize_compensation,
)
from job_ftch.application.compensation import (
    parse_compensation_text as _parse_compensation_text,  # noqa: F401 - existing parser entry point
)
from job_ftch.application.contracts import TypeChangingNode
from job_ftch.application.geo import normalize_geo_chunk, normalize_geo_sources
from job_ftch.domain import (
    JobDraft,
    JobRecord,
    Seniority,
    WorkMode,
    draft_to_record,
)

if TYPE_CHECKING:
    from job_ftch.application.contracts import Normalizer

_PREFIX_RE = re.compile(r"^(hiring|vacancy|opening|role|ищем|вакансия)\s*[:\-]\s*", re.IGNORECASE)
_COMP_SPLIT_RE = re.compile(r"\s+(?:at|@)\s+", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WS_RE.sub(" ", text).strip()


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _PREFIX_RE.sub("", _strip_html(value).strip())
    return cleaned or None


def _clean_company(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _strip_html(value).strip(" -|,")
    # Reject prose/garbage: a real company name is short and not a sentence.
    if len(cleaned) > 60 or cleaned.count(" ") >= 6:
        return None
    return cleaned or None


def _detect_work_mode(*parts: str | None) -> WorkMode:
    lowered = "\n".join(part or "" for part in parts).casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


class TitleCompanyNormalizationNode(TypeChangingNode[JobDraft, JobRecord]):
    def __init__(self, normalizer: Normalizer):
        self.normalizer = normalizer

    async def process(self, item: JobDraft) -> JobRecord | None:
        title = _clean_title(item.title_raw)
        company = _clean_company(item.company_name_raw)
        if title is not None and company is None:
            parts = _COMP_SPLIT_RE.split(title, maxsplit=1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()

        normalization_steps: list[str] = []

        role_family = item.role_family
        if role_family is None and title:
            role_family = self.normalizer.infer_role_family(title)
            if role_family:
                normalization_steps.append(f"role_family:{role_family}")

        seniority = item.seniority
        if seniority is Seniority.UNKNOWN and title:
            inferred = self.normalizer.infer_seniority(title)
            if inferred:
                try:
                    seniority = Seniority(inferred)
                    normalization_steps.append(f"seniority:{inferred}")
                except ValueError:
                    pass

        if title != item.title_raw:
            normalization_steps.append("title:cleaned")
        if company != item.company_name_raw:
            normalization_steps.append("company:cleaned")

        description = item.description_raw or ""
        if "<" in description or "&" in description:
            description = _strip_html(description)
            normalization_steps.append("description:html_stripped")

        record = draft_to_record(item)
        provenance = record.provenance.model_copy(
            update={
                "normalization": tuple(
                    list(record.provenance.normalization)
                    + normalization_steps
                    + ["title_company_normalization"]
                )
            }
        )
        return record.model_copy(
            update={
                "title": title,
                "title_normalized": title,
                "company": company,
                "company_canonical": company,
                "company_name_raw": company,
                "company_name_normalized": company,
                "description": description or record.description,
                "role_family": role_family,
                "seniority": seniority,
                "provenance": provenance,
            }
        )


class LocationWorkModeNormalizationNode:
    async def process(self, item: JobRecord) -> JobRecord | None:
        location = item.location
        work_mode = item.work_mode
        if work_mode is WorkMode.UNKNOWN:
            work_mode = _detect_work_mode(item.description, item.title, location)
        if (
            work_mode is WorkMode.UNKNOWN
            and str(item.metadata.get("job_location_type") or "").upper() == "TELECOMMUTE"
        ):
            work_mode = WorkMode.REMOTE
        remote_restrictions = item.remote_restrictions
        applicant_locations = item.metadata.get("applicant_location_requirements")
        if not remote_restrictions and applicant_locations:
            entries = (
                applicant_locations
                if isinstance(applicant_locations, list)
                else [applicant_locations]
            )
            names = [
                name
                for entry in entries
                if isinstance(entry, dict) and isinstance(name := entry.get("name"), str)
            ]
            remote_restrictions = ", ".join(names) or None
        geo = normalize_geo_sources(
            (
                item.city,
                item.country,
                location,
            )
        )
        location = geo.display
        city = geo.city or (
            normalize_geo_chunk(item.city)
            if item.city and normalize_geo_chunk(item.city) != geo.country
            else None
        )
        country = geo.country or item.country
        region = (normalize_geo_chunk(item.region) if item.region else None) or location
        normalization_steps: list[str] = []
        if location != item.location:
            normalization_steps.append("location:normalized")
        if city != item.city:
            normalization_steps.append("city:inferred")
        if country != item.country:
            normalization_steps.extend(geo.corrections or ("country:normalized",))
        if work_mode != item.work_mode:
            normalization_steps.append("work_mode:inferred")
        metadata = item.metadata
        if normalization_steps:
            metadata = {
                **item.metadata,
                "geo_normalized_location": location,
                "geo_normalized_city": city,
                "geo_normalized_country": country,
                "geo_normalization_steps": tuple(normalization_steps),
            }
        return item.model_copy(
            update={
                "location": location,
                "city": city,
                "region": region,
                "country": country,
                "work_mode": work_mode,
                "remote_restrictions": remote_restrictions,
                "metadata": metadata,
                "provenance": item.provenance.model_copy(
                    update={
                        "normalization": tuple(
                            list(item.provenance.normalization) + normalization_steps
                        )
                    }
                ),
            }
        )


class CompensationParsingNode:
    async def process(self, item: JobRecord) -> JobRecord | None:
        compensation, source, quote, reason = normalize_compensation(item)
        if source is None and compensation is item.compensation and not reason:
            return item
        metadata = dict(item.metadata)
        metadata.pop("compensation_review_reason", None)
        metadata.pop("compensation_evidence", None)
        if quote:
            metadata["compensation_evidence"] = quote
        if reason:
            metadata["compensation_review_reason"] = reason
        return item.model_copy(
            update={
                "compensation": compensation,
                "metadata": metadata,
                "provenance": item.provenance.model_copy(
                    update={
                        "normalization": tuple(
                            dict.fromkeys((*item.provenance.normalization, source))
                        )
                        if source
                        else item.provenance.normalization
                    }
                ),
            }
        )


class SkillNormalizationNode:
    def __init__(self, normalizer: Normalizer):
        self.normalizer = normalizer

    async def process(self, item: JobRecord) -> JobRecord | None:
        skills_explicit = self.normalizer.normalize_skills(item.skills_explicit)
        skills_inferred = self.normalizer.normalize_skills(item.skills_inferred)

        if skills_explicit != item.skills_explicit or skills_inferred != item.skills_inferred:
            return item.model_copy(
                update={
                    "skills_explicit": skills_explicit,
                    "skills_inferred": skills_inferred,
                    "provenance": item.provenance.model_copy(
                        update={
                            "normalization": tuple(
                                list(item.provenance.normalization) + ["skills:normalized"]
                            )
                        }
                    ),
                }
            )
        return item
