"""Deterministic extraction backend for local runs and tests."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_llm
from job_ftch.domain import EmploymentType, LanguageCode, PostType, Seniority, SkillTag, WorkMode

if TYPE_CHECKING:
    from job_ftch.config import Settings

_TITLE_RE = re.compile(r"^[^\n:]{4,120}$")
_COMPENSATION_RE = re.compile(
    r"(?P<currency>USD|EUR|GBP|KZT|\$|€|£)\s*(?P<min>\d[\d\s]{2,})"
    r"(?:\s*(?:-|to|–)\s*(?P<max>\d[\d\s]{2,}))?",
    re.IGNORECASE,
)


def _normalize_amount(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _currency_code(value: str) -> str:
    normalized = value.upper()
    return {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
    }.get(normalized, normalized)


def _detect_work_mode(text: str) -> WorkMode | None:
    lowered = text.casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return None


def _detect_language(text: str) -> LanguageCode:
    cyrillic = sum(1 for char in text if "а" <= char.casefold() <= "я" or char.casefold() == "ё")
    latin = sum(1 for char in text if "a" <= char.casefold() <= "z")
    if cyrillic == 0 and latin == 0:
        return LanguageCode.UNKNOWN
    return LanguageCode.RU if cyrillic >= latin else LanguageCode.EN


def _detect_post_type(text: str) -> PostType:
    lowered = text.casefold()
    if any(token in lowered for token in ("#candidate", "#резюме", "ищу работу", "open to work")):
        return PostType.CANDIDATE_SEEKING
    if any(token in lowered for token in ("webinar", "meetup", "course", "конференц", "митап")):
        return PostType.ANNOUNCEMENT
    if any(token in lowered for token in ("casino", "betting", "букмекер")):
        return PostType.SPAM
    return PostType.JOB_POSTING


def _detect_ai_relevance(text: str) -> float:
    lowered = text.casefold()
    keywords = (
        "ai",
        "llm",
        "ml",
        "machine learning",
        "nlp",
        "computer vision",
        "mlops",
        "rag",
        "машинн",
        "нейрон",
        "искусственн",
    )
    hits = sum(1 for token in keywords if token in lowered)
    return min(1.0, round(hits / 4.0, 2))


def _detect_seniority(text: str) -> Seniority:
    lowered = text.casefold()
    if any(token in lowered for token in ("principal", "staff")):
        return Seniority.PRINCIPAL
    if any(token in lowered for token in ("lead", "tech lead", "тимлид", "лид")):
        return Seniority.LEAD
    if any(token in lowered for token in ("senior", "старш")):
        return Seniority.SENIOR
    if any(token in lowered for token in ("middle", "mid-level")):
        return Seniority.MIDDLE
    if any(token in lowered for token in ("junior", "jun", "младш")):
        return Seniority.JUNIOR
    if "intern" in lowered or "стаж" in lowered:
        return Seniority.INTERN
    return Seniority.UNKNOWN


def _detect_employment_type(text: str) -> EmploymentType:
    lowered = text.casefold()
    if any(token in lowered for token in ("contract", "b2b", "part-time", "part time")):
        return EmploymentType.CONTRACT
    if "intern" in lowered or "стаж" in lowered:
        return EmploymentType.INTERN
    return EmploymentType.FULL_TIME


def _extract_skill_tags(text: str) -> tuple[SkillTag, ...]:
    lowered = text.casefold()
    known = (
        "python",
        "pytorch",
        "tensorflow",
        "docker",
        "kubernetes",
        "rag",
        "sql",
        "airflow",
        "fastapi",
        "llm",
        "nlp",
    )
    skills = [
        SkillTag(canonical_name=skill, source="heuristic") for skill in known if skill in lowered
    ]
    return tuple(skills)


class HeuristicLLMProvider:
    async def extract(self, text: str, schema: type[Any]) -> Any:
        lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
        title = next((line for line in lines if _TITLE_RE.match(line)), None)
        location = lines[1] if len(lines) > 1 else None
        compensation = None
        match = _COMPENSATION_RE.search(text)
        if match is not None:
            compensation = {
                "currency": _currency_code(match.group("currency")),
                "min_amount": _normalize_amount(match.group("min")),
                "max_amount": _normalize_amount(match.group("max")),
            }
        post_type = _detect_post_type(text)
        hiring_intent = 1.0 if post_type is PostType.JOB_POSTING else 0.1
        payload = {
            "title": title,
            "description": text.strip(),
            "location": location,
            "work_mode": _detect_work_mode(text),
            "compensation": compensation,
            "post_type": post_type,
            "ai_relevance": _detect_ai_relevance(text),
            "hiring_intent": hiring_intent,
            "language": _detect_language(text),
            "seniority": _detect_seniority(text),
            "employment_type": _detect_employment_type(text),
            "skills_explicit": _extract_skill_tags(text),
            "tools_stack": tuple(skill.canonical_name for skill in _extract_skill_tags(text)),
            # Plan B extensions
            "years_experience": _detect_experience(text),
            "education": _detect_education(text),
            "relocation": _detect_relocation(text),
            "visa_support": _detect_visa(text),
        }
        return schema.model_validate(payload)


def _detect_experience(text: str) -> int | None:
    # Patterns like "3+ years", "3 года", "от 3 лет", "5+ лет"
    patterns = [
        r"(\d+)\s*\+\s*years",
        r"(\d+)\s*\+\s*лет",
        r"(\d+)\s*years",
        r"от\s*(\d+)\s*лет",
        r"(\d+)\s*год",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _detect_education(text: str) -> str | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("phd", "кандидат наук")):
        return "phd"
    if any(token in lowered for token in ("master", "магистр")):
        return "master"
    if any(token in lowered for token in ("bachelor", "бакалавр", "высшее", "degree")):
        return "bachelor"
    return None


def _detect_relocation(text: str) -> bool | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("relocation", "релокация", "помощь с переездом")):
        return True
    return None


def _detect_visa(text: str) -> bool | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("visa", "виза", "sponsorship")):
        return True
    return None


@register_llm("heuristic")
def _build_heuristic_llm(settings: Settings) -> HeuristicLLMProvider:
    del settings
    return HeuristicLLMProvider()
