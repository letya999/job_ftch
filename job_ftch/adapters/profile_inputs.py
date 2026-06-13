"""Helpers for creating runtime candidate profiles from simple adapter payloads."""

from __future__ import annotations

from typing import Any

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CandidateResumeSnapshot,
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
    preferred_regions = _split_csv_like(payload.get("preferred_regions"))

    if not target_roles and summary:
        target_roles = (summary,)

    search_profile = SearchProfile(
        profile_id=profile_id,
        name=name,
        profile_description=summary,
        target_roles=target_roles,
        required_skills=_skills(required_skills),
        preferred_skills=_skills(required_skills),
        preferred_regions=preferred_regions,
    )
    return CandidateProfile(
        identity=CandidateIdentity(candidate_id=user_id, display_name=name),
        resume=CandidateResumeSnapshot(
            summary=summary,
            target_roles=target_roles,
            skills=_skills(required_skills),
        ),
        search_profiles=(search_profile,),
    )


def build_profile_catalog(profile: CandidateProfile) -> ProfileCatalog:
    return ProfileCatalog(
        catalog_name=profile.identity.display_name or profile.identity.candidate_id,
        profiles=profile.search_profiles,
    )


def build_profile_from_resume_text(
    text: str, *, user_id: str, profile_id: str | None = None
) -> ManagedCandidateProfile:
    """Extract a CandidateProfile from raw resume text using heuristics."""
    from datetime import UTC, datetime

    profile_id = profile_id or f"resume_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    name = lines[0] if lines else "Unknown"
    summary = text[:300].strip()

    # Heuristic extraction
    role_keywords = {
        "Engineer",
        "Analyst",
        "Developer",
        "Manager",
        "Lead",
        "Architect",
        "Scientist",
        "Designer",
    }
    skill_keywords = {
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
    }

    found_roles = []
    for role in role_keywords:
        if role.lower() in text.lower():
            found_roles.append(role)

    found_skills = []
    for skill in skill_keywords:
        if skill.lower() in text.lower():
            found_skills.append(skill)

    # Language detection (heuristic)
    languages = []
    if any(c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for c in text.lower()):
        languages.append("ru")
    if any(c in "abcdefghijklmnopqrstuvwxyz" for c in text.lower()):
        languages.append("en")
    if any(c in "әғқңөұүһ" for c in text.lower()):
        languages.append("kk")

    candidate_profile = build_candidate_profile_from_payload(
        user_id=user_id,
        profile_id=profile_id,
        payload={
            "name": name,
            "summary": summary,
            "target_roles": found_roles,
            "required_skills": found_skills,
            "preferred_regions": [],
        },
    )
    # Add languages and threshold
    search_profiles = list(candidate_profile.search_profiles)
    if search_profiles:
        sp = search_profiles[0]
        search_profiles[0] = sp.model_copy(
            update={
                "languages_of_interest": tuple(languages),
                "relevance_threshold": 0.3,
            }
        )
    candidate_profile = candidate_profile.model_copy(
        update={
            "search_profiles": tuple(search_profiles),
            "resume": CandidateResumeSnapshot(
                raw_text=text[:5000],
                summary=summary,
                target_roles=tuple(found_roles),
                skills=_skills(tuple(found_skills)),
            ),
        }
    )

    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=candidate_profile,
        updated_at=datetime.now(UTC),
    )
