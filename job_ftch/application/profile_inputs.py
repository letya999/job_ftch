"""Helpers for creating runtime candidate profiles from simple adapter payloads."""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CandidateResumeSnapshot,
    LanguageCode,
    ManagedCandidateProfile,
    ProfileCatalog,
    SearchProfile,
    SkillTag,
)


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
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    summary: str | None = None
    target_roles: tuple[str, ...] = ()
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    target_domains: tuple[str, ...] = ()
    anti_preferences: tuple[str, ...] = ()
    preferred_regions: tuple[str, ...] = ()
    preferred_countries: tuple[str, ...] = ()
    preferred_cities: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    seniority_hint: str | None = None
    keywords: tuple[str, ...] = Field(default_factory=tuple)


def _heuristic_resume_payload(text: str) -> ResumeExtractionPayload:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    name = lines[0] if lines else "Unknown"
    summary = text[:500].strip() or None

    role_keywords = (
        "AI Engineer",
        "ML Engineer",
        "LLM Engineer",
        "MLOps Engineer",
        "Data Scientist",
        "Product Engineer",
        "Python Developer",
        "Developer",
        "Lead",
        "Architect",
        "Analyst",
        "Automation Engineer",
    )
    skill_keywords = (
        "Python",
        "SQL",
        "Java",
        "Go",
        "TypeScript",
        "React",
        "Postgres",
        "Kubernetes",
        "Docker",
        "Spark",
        "dbt",
        "Kafka",
        "Airflow",
        "OpenAI",
        "Qdrant",
        "n8n",
        "Claude Code",
        "MCP",
        "Prompting",
        "RAG",
        "Embeddings",
        "Evaluation",
        "FastAPI",
    )
    domain_keywords = ("AI", "Machine Learning", "LLM", "Automation", "MLOps", "Data Platform")

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
        anti_preferences=anti_preferences or (
            "1c", "recruiter", "sap", "accounting",
        ),
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
        catalog_name=profile.identity.display_name or profile.identity.candidate_id,
        profiles=profile.search_profiles,
    )


async def _extract_resume_payload(
    text: str,
    *,
    llm_provider: object | None = None,
) -> ResumeExtractionPayload:
    if llm_provider is not None:
        try:
            extract = getattr(llm_provider, "extract", None)
            if callable(extract):
                result = await extract(text[:12000], ResumeExtractionPayload)
                logger.info(
                    "resume_extraction_llm",
                    roles=result.target_roles,
                    skills=result.required_skills,
                )
                return result
        except Exception as exc:
            logger.warning("resume_extraction_llm_failed", error=str(exc), exc_info=True)
    logger.warning(
        "resume_extraction_heuristic_fallback",
        has_provider=llm_provider is not None,
    )
    return _heuristic_resume_payload(text)


async def build_profile_from_resume_text_async(
    text: str,
    *,
    user_id: str,
    profile_id: str | None = None,
    llm_provider: object | None = None,
) -> ManagedCandidateProfile:
    """Extract a CandidateProfile from raw resume text using LLM with heuristic fallback."""
    from datetime import UTC, datetime

    profile_id = profile_id or f"resume_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    extracted = await _extract_resume_payload(text, llm_provider=llm_provider)
    summary = (extracted.summary or text[:500].strip())[:1200] or None
    display_name = (extracted.name or "").strip()
    if not display_name:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        display_name = lines[0] if lines else "Unknown"

    candidate_profile = build_candidate_profile_from_payload(
        user_id=user_id,
        profile_id=profile_id,
        payload={
            "name": display_name,
            "summary": summary,
            "target_roles": extracted.target_roles,
            "required_skills": _merge_strings(extracted.required_skills, extracted.keywords),
            "preferred_skills": _merge_strings(extracted.preferred_skills, extracted.required_skills),
            "target_domains": extracted.target_domains,
            "anti_preferences": extracted.anti_preferences,
            "preferred_regions": extracted.preferred_regions,
            "preferred_countries": extracted.preferred_countries,
            "preferred_cities": extracted.preferred_cities,
            "allowed_languages": extracted.languages,
        },
    )
    search_profiles = list(candidate_profile.search_profiles)
    if search_profiles:
        from job_ftch.domain.profile import ProfileWeights

        sp = search_profiles[0]
        search_profiles[0] = sp.model_copy(
            update={
                "allowed_languages": _normalize_language_codes(extracted.languages),
                "relevance_threshold": 0.35,
                # Hybrid search: let example embeddings dominate ranking so jobs
                # that "look like" the positive resumes win and negatives sink.
                "weights": ProfileWeights(
                    title=0.15,
                    semantic_role=0.1,
                    skills=0.15,
                    domain=0.08,
                    seniority=0.05,
                    region=0.04,
                    salary=0.03,
                    culture=0.0,
                    vector=0.4,
                ),
            }
        )
    candidate_profile = candidate_profile.model_copy(
        update={
            "search_profiles": tuple(search_profiles),
            "resume": CandidateResumeSnapshot(
                raw_text=text[:5000],
                summary=summary,
                target_roles=tuple(extracted.target_roles),
                skills=_skills(
                    _merge_strings(extracted.required_skills, extracted.preferred_skills)
                ),
                seniority_hint=extracted.seniority_hint,
            ),
        }
    )

    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=candidate_profile,
        updated_at=datetime.now(UTC),
    )


def build_profile_from_resume_text(
    text: str, *, user_id: str, profile_id: str | None = None
) -> ManagedCandidateProfile:
    """Synchronous heuristic-only helper kept for compatibility in tests and non-async callers."""
    import asyncio
    from datetime import UTC, datetime

    resolved_profile_id = profile_id or f"resume_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            build_profile_from_resume_text_async(
                text,
                user_id=user_id,
                profile_id=resolved_profile_id,
                llm_provider=None,
            )
        )
    heuristic = _heuristic_resume_payload(text)
    candidate_profile = build_candidate_profile_from_payload(
        user_id=user_id,
        profile_id=resolved_profile_id,
        payload={
            "name": heuristic.name,
            "summary": heuristic.summary,
            "target_roles": heuristic.target_roles,
            "required_skills": heuristic.required_skills,
            "preferred_skills": heuristic.preferred_skills,
            "target_domains": heuristic.target_domains,
            "allowed_languages": heuristic.languages,
        },
    )
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=resolved_profile_id,
        profile=candidate_profile,
        updated_at=datetime.now(UTC),
    )


def merge_resume_profile(
    existing: ManagedCandidateProfile,
    extracted: ManagedCandidateProfile,
    *,
    is_negative: bool,
) -> ManagedCandidateProfile:
    from datetime import UTC, datetime

    if not existing.profile.search_profiles or not extracted.profile.search_profiles:
        return existing

    current_sp = existing.profile.search_profiles[0]
    extracted_sp = extracted.profile.search_profiles[0]
    resume_text = (extracted.profile.resume.raw_text if extracted.profile.resume else "") or ""
    summary = existing.profile.resume.summary if existing.profile.resume else None
    if not summary and extracted.profile.resume is not None:
        summary = extracted.profile.resume.summary

    if is_negative:
        updated_sp = current_sp.model_copy(
            update={
                "negative_example_texts": _merge_strings(
                    current_sp.negative_example_texts,
                    (resume_text,) if resume_text else (),
                ),
                "anti_preferences": _merge_strings(
                    current_sp.anti_preferences,
                    extracted_sp.target_roles,
                    extracted_sp.target_domains,
                    extracted_sp.anti_preferences,
                ),
                "negative_embedding_vectors": (),
            }
        )
    else:
        updated_sp = current_sp.model_copy(
            update={
                "profile_description": current_sp.profile_description or extracted_sp.profile_description,
                "target_roles": _merge_strings(current_sp.target_roles, extracted_sp.target_roles),
                "target_domains": _merge_strings(
                    current_sp.target_domains, extracted_sp.target_domains
                ),
                "required_skills": _merge_skills(
                    current_sp.required_skills, extracted_sp.required_skills
                ),
                "preferred_skills": _merge_skills(
                    current_sp.preferred_skills,
                    extracted_sp.preferred_skills,
                    extracted_sp.required_skills,
                ),
                "preferred_regions": _merge_strings(
                    current_sp.preferred_regions, extracted_sp.preferred_regions
                ),
                "preferred_countries": _merge_strings(
                    current_sp.preferred_countries, extracted_sp.preferred_countries
                ),
                "preferred_cities": _merge_strings(
                    current_sp.preferred_cities, extracted_sp.preferred_cities
                ),
                "allowed_languages": tuple(
                    dict.fromkeys(current_sp.allowed_languages + extracted_sp.allowed_languages)
                ),
                "positive_example_texts": _merge_strings(
                    current_sp.positive_example_texts,
                    (resume_text,) if resume_text else (),
                ),
                "embedding_vector": None,
            }
        )

    updated_profiles = (updated_sp,) + existing.profile.search_profiles[1:]
    updated_resume = CandidateResumeSnapshot(
        raw_text=(existing.profile.resume.raw_text if existing.profile.resume else None) or resume_text,
        summary=summary,
        skills=_merge_skills(
            existing.profile.resume.skills if existing.profile.resume else (),
            extracted.profile.resume.skills if extracted.profile.resume else (),
        ),
        target_roles=_merge_strings(
            existing.profile.resume.target_roles if existing.profile.resume else (),
            extracted.profile.resume.target_roles if extracted.profile.resume else (),
        ),
        seniority_hint=(
            (existing.profile.resume.seniority_hint if existing.profile.resume else None)
            or (extracted.profile.resume.seniority_hint if extracted.profile.resume else None)
        ),
    )
    updated_candidate = existing.profile.model_copy(
        update={"search_profiles": updated_profiles, "resume": updated_resume}
    )
    return ManagedCandidateProfile(
        user_id=existing.user_id,
        profile_id=existing.profile_id,
        profile=updated_candidate,
        created_at=existing.created_at,
        updated_at=datetime.now(UTC),
    )


def add_example_to_profile(
    managed: ManagedCandidateProfile,
    text: str,
    *,
    kind: str,  # "positive_resume", "negative_resume", "positive_job", "negative_job"
) -> ManagedCandidateProfile:
    """Add a text example to the first search profile of the candidate."""
    from datetime import UTC, datetime

    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]
    text_trimmed = text.strip()[:5000]
    if kind.startswith("negative"):
        updated_sp = sp.model_copy(
            update={"negative_example_texts": sp.negative_example_texts + (text_trimmed,)}
        )
    else:
        updated_sp = sp.model_copy(
            update={"positive_example_texts": sp.positive_example_texts + (text_trimmed,)}
        )
    updated_profiles = (updated_sp,) + managed.profile.search_profiles[1:]
    updated_profile = managed.profile.model_copy(update={"search_profiles": updated_profiles})
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )


async def embed_profile_examples(
    managed: ManagedCandidateProfile,
    embedding_provider: object,  # EmbeddingProvider Protocol
) -> ManagedCandidateProfile:
    """Compute embedding vectors for profile's example texts and store them."""
    from datetime import UTC, datetime

    if not managed.profile.search_profiles:
        return managed

    updated_profiles = list(managed.profile.search_profiles)
    for i, sp in enumerate(updated_profiles):
        pos_texts = list(sp.positive_example_texts) + (
            [managed.profile.resume.raw_text]
            if managed.profile.resume and managed.profile.resume.raw_text
            else []
        )
        neg_texts = list(sp.negative_example_texts)

        pos_vector: tuple[float, ...] | None = None
        neg_vectors: tuple[tuple[float, ...], ...] = ()

        if pos_texts:
            try:
                _embed_fn = getattr(
                    embedding_provider, "embed_query", getattr(embedding_provider, "embed", None)
                )
                if _embed_fn:
                    vecs = await _embed_fn(pos_texts)
                    if vecs:
                        dim = len(vecs[0])
                        avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
                        pos_vector = tuple(avg)
            except Exception:
                pass

        if neg_texts:
            try:
                _embed_fn = getattr(
                    embedding_provider, "embed_query", getattr(embedding_provider, "embed", None)
                )
                if _embed_fn:
                    vecs = await _embed_fn(neg_texts)
                    neg_vectors = tuple(tuple(v) for v in vecs)
            except Exception:
                pass

        updated_profiles[i] = sp.model_copy(
            update={
                "embedding_vector": pos_vector,
                "negative_embedding_vectors": neg_vectors,
            }
        )

    updated_profile = managed.profile.model_copy(
        update={"search_profiles": tuple(updated_profiles)}
    )
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )


def remove_example_from_profile(
    managed: ManagedCandidateProfile,
    kind: str,
    index: int,
) -> ManagedCandidateProfile:
    """Remove an example text from the first search profile by type and index."""
    from datetime import UTC, datetime

    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]

    is_negative = kind.startswith("negative")
    texts = sp.negative_example_texts if is_negative else sp.positive_example_texts

    if index < 0 or index >= len(texts):
        return managed

    new_texts = texts[:index] + texts[index + 1 :]

    if is_negative:
        updated_sp = sp.model_copy(update={"negative_example_texts": new_texts})
    else:
        updated_sp = sp.model_copy(update={"positive_example_texts": new_texts})

    updated_profiles = (updated_sp,) + managed.profile.search_profiles[1:]
    updated_profile = managed.profile.model_copy(update={"search_profiles": updated_profiles})
    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )


def list_examples(managed: ManagedCandidateProfile) -> dict[str, list[str]]:
    """Return all example texts grouped by kind from the first search profile."""
    if not managed.profile.search_profiles:
        return {
            "positive_resume": [],
            "negative_resume": [],
            "positive_job": [],
            "negative_job": [],
        }
    sp = managed.profile.search_profiles[0]
    pos = list(sp.positive_example_texts)
    neg = list(sp.negative_example_texts)
    return {
        "positive_resume": pos,
        "negative_resume": neg,
        "positive_job": [],
        "negative_job": [],
    }
