"""Normalization stages for structured Job fields."""

from __future__ import annotations

import re

from job_ftch.domain import CompensationRange, JobDraft, JobRecord, WorkMode, draft_to_record

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


class TitleCompanyNormalizationNode:
    async def process(self, item: JobDraft) -> JobRecord | None:
        title = _clean_title(item.title_raw)
        company = _clean_company(item.company_name_raw)
        if title is not None and company is None:
            parts = _COMP_SPLIT_RE.split(title, maxsplit=1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()
        role_family = item.role_family
        lowered_title = (title or "").casefold()
        if role_family is None:
            if any(token in lowered_title for token in ("engineer", "developer", "разработ")):
                role_family = "engineering"
            elif any(token in lowered_title for token in ("scientist", "research", "исслед")):
                role_family = "research"
            elif any(token in lowered_title for token in ("manager", "product", "менедж")):
                role_family = "product"
        record = draft_to_record(item)
        return record.model_copy(
            update={
                "title": title,
                "title_normalized": title,
                "company": company,
                "company_canonical": company,
                "company_name_raw": company,
                "company_name_normalized": company,
                "role_family": role_family,
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
        return item.model_copy(
            update={
                "location": location,
                "city": city,
                "region": region,
                "work_mode": work_mode,
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
        return item.model_copy(update={"compensation": compensation})
