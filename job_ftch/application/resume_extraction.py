"""LLM-aware resume extraction (per ADR-027).

Owns:
  - `_extract_resume_payload(text, *, llm_provider=None)` — uses the
    LLM when available, falls back to the heuristic in
    `profile_parsing.py` otherwise.
  - `build_profile_from_resume_text_async(text, *, user_id, ...)` —
    async API that builds a `ManagedCandidateProfile` from a free-form
    resume text.
  - `build_profile_from_resume_text(text, ...)` — sync wrapper.
  - `merge_resume_profile(...)` — merges positive/negative examples into
    an existing profile.
  - `add_example_to_profile(managed, kind, text, ...)` — adds one
    example of the given kind to the first search profile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from job_ftch.application.profile_parsing import (
    TUNED_PROFILE_WEIGHTS,
    TUNED_RELEVANCE_THRESHOLD,
    ResumeExtractionPayload,
    _heuristic_resume_payload,
    _merge_skills,
    _merge_strings,
    _normalize_language_codes,
    _skills,
    build_candidate_profile_from_payload,
)

if TYPE_CHECKING:
    from job_ftch.domain import ManagedCandidateProfile


logger = structlog.get_logger(__name__)


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
                if isinstance(result, ResumeExtractionPayload):
                    return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "resume_llm_extract_failed_falling_back_to_heuristic",
                error=str(exc),
            )
    return _heuristic_resume_payload(text)


async def build_profile_from_resume_text_async(
    text: str,
    *,
    user_id: str,
    profile_id: str | None = None,
    llm_provider: object | None = None,
) -> ManagedCandidateProfile:
    """Extract a `ManagedCandidateProfile` from raw resume text using
    LLM with heuristic fallback. Sets default profile weights biased
    toward vector search (0.4 weight) so jobs that look like the
    positive resumes win and negatives sink."""
    from datetime import UTC, datetime

    from job_ftch.domain import ManagedCandidateProfile
    from job_ftch.domain.candidate import CandidateResumeSnapshot

    profile_id = profile_id or f"resume_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    extracted = await _extract_resume_payload(text, llm_provider=llm_provider)
    summary = (extracted.summary or text[:500].strip())[:1200] or None
    display_name = (extracted.name or "").strip()
    if not display_name:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        display_name = lines[0] if lines else "Unknown"

    # The validator on ResumeExtractionPayload already normalises None
    # to () for every collection field, so we can rely on
    # ``extracted.<field>`` being a concrete tuple here. Defensive
    # ``or ()`` keeps the code robust against callers that hand-craft
    # a payload (e.g. tests or future programmatic clients).
    candidate_profile = build_candidate_profile_from_payload(
        user_id=user_id,
        profile_id=profile_id,
        payload={
            "name": display_name,
            "summary": summary,
            "target_roles": extracted.target_roles or (),
            "required_skills": _merge_strings(
                extracted.required_skills or (), extracted.keywords or ()
            ),
            "preferred_skills": _merge_strings(
                extracted.preferred_skills or (), extracted.required_skills or ()
            ),
            "target_domains": extracted.target_domains or (),
            "anti_preferences": extracted.anti_preferences or (),
            "preferred_regions": extracted.preferred_regions or (),
            "preferred_countries": extracted.preferred_countries or (),
            "preferred_cities": extracted.preferred_cities or (),
            "allowed_languages": extracted.languages or (),
        },
    )
    search_profiles = list(candidate_profile.search_profiles)
    if search_profiles:
        sp = search_profiles[0]
        search_profiles[0] = sp.model_copy(
            update={
                "allowed_languages": _normalize_language_codes(extracted.languages or ()),
                "relevance_threshold": TUNED_RELEVANCE_THRESHOLD,
                "weights": TUNED_PROFILE_WEIGHTS,
            }
        )
    candidate_profile = candidate_profile.model_copy(
        update={
            "search_profiles": tuple(search_profiles),
            "resume": CandidateResumeSnapshot(
                raw_text=text[:5000],
                summary=summary,
                target_roles=tuple(extracted.target_roles or ()),
                skills=_skills(
                    _merge_strings(
                        extracted.required_skills or (),
                        extracted.preferred_skills or (),
                    )
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
    text: str,
    *,
    user_id: str,
    profile_id: str | None = None,
    llm_provider: object | None = None,  # kept for API compat; ignored in sync path
) -> ManagedCandidateProfile:
    """Synchronous heuristic-only helper kept for compatibility in tests
    and non-async callers. When called outside a running event loop, this
    delegates to the async API (which honours llm_provider). When called
    inside a running event loop, falls back to the heuristic path
    directly (asyncio.run cannot be called from a running loop)."""
    import asyncio
    from datetime import UTC, datetime

    from job_ftch.domain import ManagedCandidateProfile

    resolved_profile_id = profile_id or f"resume_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            build_profile_from_resume_text_async(
                text,
                user_id=user_id,
                profile_id=resolved_profile_id,
                llm_provider=llm_provider,
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
    is_negative: bool = False,
) -> ManagedCandidateProfile:
    """Merge extracted resume metadata and text into existing profile.

    Per BR-DROP-LANG: ``allowed_languages`` is a UNION of the two
    profiles, not a replace. Previously the merge only touched
    ``target_roles`` and ``required_skills`` and silently dropped
    ``allowed_languages`` if the extracted profile was empty — the
    result was that a single heuristic-extracted resume would leave
    the user's language preferences in a half-applied state and the
    language hard-filter would reject almost every job the user
    actually cared about. We now merge the field explicitly.
    """
    from datetime import UTC, datetime

    from job_ftch.domain import ManagedCandidateProfile

    if not existing.profile.search_profiles or not extracted.profile.search_profiles:
        return existing

    sp_ex = existing.profile.search_profiles[0]
    sp_new = extracted.profile.search_profiles[0]

    # Add raw resume text as example shot
    raw_text = ""
    if extracted.profile.resume and extracted.profile.resume.raw_text:
        raw_text = extracted.profile.resume.raw_text[:5000]

    if is_negative:
        new_neg = sp_ex.negative_example_texts + ((raw_text,) if raw_text else ())
        updated_sp = sp_ex.model_copy(
            update={
                "negative_example_texts": new_neg,
            }
        )
    else:
        # Positive resume uploads refine the user's role/skill/language profile.
        merged_roles = _merge_strings(sp_ex.target_roles, sp_new.target_roles)
        merged_skills = _merge_skills(sp_ex.required_skills, sp_new.required_skills)
        merged_soft_pref = _merge_strings(sp_ex.soft_preferences, sp_new.soft_preferences)
        merged_languages = _merge_strings(
            tuple(lang.value for lang in sp_ex.allowed_languages),
            tuple(lang.value for lang in sp_new.allowed_languages),
        )
        new_pos = sp_ex.positive_example_texts + ((raw_text,) if raw_text else ())
        updated_sp = sp_ex.model_copy(
            update={
                "target_roles": merged_roles,
                "required_skills": merged_skills,
                "soft_preferences": tuple(merged_soft_pref),
                "allowed_languages": tuple(merged_languages),
                "positive_example_texts": new_pos,
            }
        )

    updated_profile = existing.profile.model_copy(
        update={"search_profiles": (updated_sp,) + existing.profile.search_profiles[1:]}
    )
    return ManagedCandidateProfile(
        user_id=existing.user_id,
        profile_id=existing.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )


def add_example_to_profile(
    managed: ManagedCandidateProfile,
    text: str,
    *,
    kind: str,
) -> ManagedCandidateProfile:
    """Add a text example to the first search profile of the candidate."""
    from datetime import UTC, datetime

    if not managed.profile.search_profiles:
        return managed
    sp = managed.profile.search_profiles[0]
    text_trimmed = text.strip()[:5000]
    if kind == "positive_job":
        updated_sp = sp.model_copy(
            update={"positive_job_example_texts": sp.positive_job_example_texts + (text_trimmed,)}
        )
    elif kind == "negative_job":
        updated_sp = sp.model_copy(
            update={"negative_job_example_texts": sp.negative_job_example_texts + (text_trimmed,)}
        )
    elif kind.startswith("negative"):
        updated_sp = sp.model_copy(
            update={"negative_example_texts": sp.negative_example_texts + (text_trimmed,)}
        )
    else:
        updated_sp = sp.model_copy(
            update={"positive_example_texts": sp.positive_example_texts + (text_trimmed,)}
        )
    updated_profiles = (updated_sp,) + managed.profile.search_profiles[1:]
    updated_profile = managed.profile.model_copy(update={"search_profiles": updated_profiles})
    from job_ftch.domain import ManagedCandidateProfile

    return ManagedCandidateProfile(
        user_id=managed.user_id,
        profile_id=managed.profile_id,
        profile=updated_profile,
        updated_at=datetime.now(UTC),
    )
