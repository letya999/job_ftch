"""Profile parsing: pure-data helpers for turning adapter payloads into a
`SearchProfile` (per ADR-018 / ADR-027).

This module owns:
  - CSV-like / language code / skills / strings merging utilities
  - `ResumeExtractionPayload` Pydantic model
  - Heuristic resume payload extraction (`_heuristic_resume_payload`)
  - `build_candidate_profile_from_payload` and `build_profile_catalog`
  - `_detect_text_language_simple` (cyrillic / latin / kazakh hint)

LLM-based resume extraction lives in `resume_extraction.py`; ontology
shot enrichment lives in `ontology_enrichment.py`. `profile_inputs.py`
is the thin orchestrator that wires them together.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, model_validator

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CandidateResumeSnapshot,
    LanguageCode,
    ProfileCatalog,
    SearchProfile,
    SkillTag,
)
from job_ftch.domain.profile import ProfileWeights

logger = structlog.get_logger(__name__)

TUNED_PROFILE_WEIGHTS = ProfileWeights(
    title=0.15,
    semantic_role=0.1,
    skills=0.15,
    domain=0.08,
    seniority=0.05,
    region=0.04,
    salary=0.03,
    culture=0.0,
    vector=0.4,
)
TUNED_RELEVANCE_THRESHOLD = 0.35


def _detect_text_language_simple(text: str) -> str:
    """Heuristic language hint: "ru" / "kk" / "en" / "unknown" based on
    Cyrillic / Latin / Kazakh-specific character density. Used by ontology
    enrichment to pick the lang tag for `upsert_skill(..., lang=lang)`.
    """
    if not text:
        return "unknown"
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    kazakh_specific = sum(1 for c in text if c in "\u04ae\u04af\u04b0\u04b1\u04ba")
    total = max(cyrillic + latin, 1)
    if kazakh_specific > 0 and cyrillic / total > 0.5:
        return "kk"
    if cyrillic / total > 0.4:
        return "ru"
    if latin / total > 0.4:
        return "en"
    return "unknown"


def _split_csv_like(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _skills(values: tuple[str, ...]) -> tuple[SkillTag, ...]:
    return tuple(SkillTag(canonical_name=value, source="runtime_profile") for value in values)


def _normalize_language_codes(values: tuple[str, ...]) -> tuple[LanguageCode, ...]:
    normalized: list[LanguageCode] = []
    mapping = {
        "ru": LanguageCode.RU,
        "russian": LanguageCode.RU,
        "en": LanguageCode.EN,
        "english": LanguageCode.EN,
        "kk": LanguageCode.KK,
        "kazakh": LanguageCode.KK,
    }
    for value in values:
        code = mapping.get(value.strip().casefold())
        if code and code not in normalized:
            normalized.append(code)
    return tuple(normalized)


def _merge_strings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            cleaned = value.strip()
            if not cleaned:
                continue
            marker = cleaned.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(cleaned)
    return tuple(merged)


def _merge_skills(*groups: tuple[SkillTag, ...]) -> tuple[SkillTag, ...]:
    merged: list[SkillTag] = []
    seen: set[str] = set()
    for group in groups:
        for skill in group:
            marker = skill.canonical_name.strip().casefold()
            if not marker or marker in seen:
                continue
            seen.add(marker)
            merged.append(skill)
    return tuple(merged)


class ResumeExtractionPayload(BaseModel):
    """Pydantic model for the LLM-extracted resume payload.

    Every collection field is tolerant of ``None`` because GPT-class
    models occasionally return ``null`` for optional arrays; we coerce
    to an empty tuple in :meth:`_coerce_none_to_empty` so the rest
    of the pipeline can treat the payload uniformly.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    summary: str | None = None
    target_roles: tuple[str, ...] | None = None
    required_skills: tuple[str, ...] | None = None
    preferred_skills: tuple[str, ...] | None = None
    target_domains: tuple[str, ...] | None = None
    anti_preferences: tuple[str, ...] | None = None
    preferred_regions: tuple[str, ...] | None = None
    preferred_countries: tuple[str, ...] | None = None
    preferred_cities: tuple[str, ...] | None = None
    languages: tuple[str, ...] | None = None
    seniority_hint: str | None = None
    keywords: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _coerce_none_to_empty(self) -> ResumeExtractionPayload:
        """Treat ``None`` from the LLM the same as an empty tuple.

        Previously a Pydantic ``tuple[str, ...]`` field rejected
        ``None`` with ``Input should be a valid array [type=tuple_type,
        input_value=None, input_type=NoneType]``, which made the entire
        extraction call fail and forced a heuristic fallback for any
        optional list the model happened to omit. We now treat
        ``None`` as ``()`` so the rest of the pipeline keeps the LLM's
        other (correct) fields instead of throwing them away.
        """
        for field_name in (
            "target_roles",
            "required_skills",
            "preferred_skills",
            "target_domains",
            "anti_preferences",
            "preferred_regions",
            "preferred_countries",
            "preferred_cities",
            "languages",
            "keywords",
        ):
            value = getattr(self, field_name)
            if value is None:
                object.__setattr__(self, field_name, ())
        return self


def _heuristic_resume_payload(
    text: str,
    *,
    role_keywords: tuple[str, ...] = (),
    skill_keywords: tuple[str, ...] = (),
    domain_keywords: tuple[str, ...] = (),
) -> ResumeExtractionPayload:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    name = lines[0] if lines else "Unknown"
    summary = text[:500].strip() or None

    lowered = text.lower()
    found_roles = tuple(role for role in role_keywords if role.lower() in lowered)
    found_skills = tuple(skill for skill in skill_keywords if skill.lower() in lowered)
    found_domains = tuple(domain for domain in domain_keywords if domain.lower() in lowered)

    languages: list[str] = []
    if any(c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for c in lowered):
        languages.append("ru")
    if any(c in "abcdefghijklmnopqrstuvwxyz" for c in lowered):
        languages.append("en")
    if any(c in "әғқңөұүһ" for c in lowered):
        languages.append("kk")

    return ResumeExtractionPayload(
        name=name,
        summary=summary,
        target_roles=found_roles,
        required_skills=found_skills,
        preferred_skills=found_skills,
        target_domains=found_domains,
        languages=tuple(languages),
        keywords=_merge_strings(found_roles, found_skills, found_domains),
    )


def build_candidate_profile_from_payload(
    *,
    user_id: str,
    profile_id: str,
    payload: dict[str, Any],
) -> CandidateProfile:
    name = str(payload.get("name") or profile_id).strip() or profile_id
    summary = str(payload.get("summary") or "").strip() or None
    target_roles = _split_csv_like(payload.get("target_roles") or payload.get("roles"))
    required_skills = _split_csv_like(payload.get("required_skills") or payload.get("skills"))
    preferred_skills = _split_csv_like(payload.get("preferred_skills")) or required_skills
    preferred_regions = _split_csv_like(payload.get("preferred_regions"))
    preferred_countries = _split_csv_like(payload.get("preferred_countries"))
    preferred_cities = _split_csv_like(payload.get("preferred_cities"))
    target_domains = _split_csv_like(payload.get("target_domains"))
    anti_preferences = _split_csv_like(payload.get("anti_preferences"))
    allowed_languages = _normalize_language_codes(_split_csv_like(payload.get("allowed_languages")))

    if not target_roles and summary:
        target_roles = (summary,)

    # Populate soft_preferences from union of required and preferred skill names
    # so SemanticPrefilterNode can score skill-related job posts before LLM extraction.
    soft_pref_names = tuple(dict.fromkeys(list(required_skills) + list(preferred_skills)))

    search_profile = SearchProfile(
        profile_id=profile_id,
        name=name,
        profile_description=summary,
        target_roles=target_roles,
        target_domains=target_domains,
        required_skills=_skills(required_skills),
        preferred_skills=_skills(preferred_skills),
        soft_preferences=soft_pref_names,
        anti_preferences=anti_preferences,
        preferred_regions=preferred_regions,
        preferred_countries=preferred_countries,
        preferred_cities=preferred_cities,
        allowed_languages=allowed_languages,
    )
    return CandidateProfile(
        identity=CandidateIdentity(candidate_id=user_id, display_name=name),
        resume=CandidateResumeSnapshot(
            summary=summary,
            target_roles=target_roles,
            skills=_skills(_merge_strings(required_skills, preferred_skills)),
        ),
        search_profiles=(search_profile,),
    )


def build_profile_catalog(profile: CandidateProfile) -> ProfileCatalog:
    return ProfileCatalog(
        catalog_name=profile.identity.candidate_id,
        profiles=profile.search_profiles,
    )
