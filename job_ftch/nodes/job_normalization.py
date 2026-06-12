"""Normalization stages for structured Job fields."""

from __future__ import annotations

import re

from job_ftch.application.contracts import TypeChangingNode
from job_ftch.domain import (
    CompensationRange,
    JobDraft,
    JobRecord,
    Seniority,
    WorkMode,
    draft_to_record,
)
from job_ftch.infrastructure.ontology.normalizer import OntologyNormalizer, get_default_normalizer

_PREFIX_RE = re.compile(r"^(hiring|vacancy|opening|role|ищем|вакансия)\s*[:\-]\s*", re.IGNORECASE)
_COMP_SPLIT_RE = re.compile(r"\s+(?:at|@|-)\s+", re.IGNORECASE)
_SALARY_RE = re.compile(
    r"(?P<currency>USD|EUR|GBP|KZT|\$|€|£)\s*(?P<min>\d[\d\s]{2,})"
    r"(?:\s*(?:-|to|–)\s*(?P<max>\d[\d\s]{2,}))?",
    re.IGNORECASE,
)


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _PREFIX_RE.sub("", value.strip())
    return cleaned or None


def _clean_company(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip(" -|,")
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


def _normalize_amount(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _normalize_currency(value: str) -> str:
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(value.upper(), value.upper())


class TitleCompanyNormalizationNode(TypeChangingNode[JobDraft, JobRecord]):
    def __init__(self, normalizer: OntologyNormalizer | None = None):
        self.normalizer = normalizer or get_default_normalizer()

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
        
        record = draft_to_record(item)
        provenance = record.provenance.model_copy(
            update={
                "normalization": tuple(
                    list(record.provenance.normalization) + normalization_steps + ["title_company_normalization"]
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
        if location is not None:
            normalized = location.strip()
            lowered = normalized.casefold()
            if lowered in {"remote", "hybrid", "on-site", "onsite"}:
                location = None
        city = item.city or location
        region = item.region or location
        normalization_steps: list[str] = []
        if location != item.location:
            normalization_steps.append("location:normalized")
        if work_mode != item.work_mode:
            normalization_steps.append("work_mode:inferred")
        return item.model_copy(
            update={
                "location": location,
                "city": city,
                "region": region,
                "work_mode": work_mode,
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
        if item.compensation is not None:
            return item
        match = _SALARY_RE.search(item.description)
        if match is None:
            return item
        compensation = CompensationRange(
            currency=_normalize_currency(match.group("currency")),
            min_amount=_normalize_amount(match.group("min")),
            max_amount=_normalize_amount(match.group("max")),
        )
        return item.model_copy(
            update={
                "compensation": compensation,
                "provenance": item.provenance.model_copy(
                    update={
                        "normalization": tuple(
                            list(item.provenance.normalization) + ["compensation:parsed_from_description"]
                        )
                    }
                ),
            }
        )


class SkillNormalizationNode:
    def __init__(self, normalizer: OntologyNormalizer | None = None):
        self.normalizer = normalizer or get_default_normalizer()

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
