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
    updated_profile = managed.profile.model_copy(
        update={"search_profiles": updated_profiles}
    )
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
                # Type hint for mypy if needed, but we keep it dynamic for Protocol
                vecs = await embedding_provider.embed(pos_texts)  # type: ignore[attr-defined]
                if vecs:
                    # Average positive example vectors
                    dim = len(vecs[0])
                    avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
                    pos_vector = tuple(avg)
            except Exception:
                pass  # embedding failed, skip

        if neg_texts:
            try:
                vecs = await embedding_provider.embed(neg_texts)  # type: ignore[attr-defined]
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
