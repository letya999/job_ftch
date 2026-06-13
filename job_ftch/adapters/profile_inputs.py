"""Helpers for creating runtime candidate profiles from simple adapter payloads."""

from __future__ import annotations

from typing import Any

from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    CandidateResumeSnapshot,
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
