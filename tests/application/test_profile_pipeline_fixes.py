"""Regression tests for fixes the first ``/run`` / LLM extraction / language gate.

These three fixes are bundled together because the original code
path they corrected is small, easy to regress, and important to
keep stable:

1. ``merge_resume_profile`` must merge ``allowed_languages`` and
   ``soft_preferences`` (not just ``target_roles`` / ``required_skills``).
2. ``has_candidate_profile_data`` must accept a profile that has at
   least one example of any kind, not require the active marker to
   be set.
3. ``HardFilterNode._language_allowed`` must be permissive by
   default — accept all languages unless the user has explicitly
   populated the allow-list. ``MatchScoring`` should also be lenient
   on the ``unknown`` language.
4. ``ResumeExtractionPayload`` must coerce ``None`` -> empty tuple so
   GPT's occasional ``null`` doesn't blow up the LLM extraction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_ftch.application.profile_parsing import (
    ResumeExtractionPayload,
)
from job_ftch.application.resume_extraction import merge_resume_profile
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    LanguageCode,
    ManagedCandidateProfile,
    SearchProfile,
)
from job_ftch.domain.profile import ProfileCatalog
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    user_id: str = "u1",
    profile_id: str = "user_u1",
    pos: tuple[str, ...] = (),
    neg: tuple[str, ...] = (),
    pos_job: tuple[str, ...] = (),
    neg_job: tuple[str, ...] = (),
    allowed_languages: tuple[LanguageCode, ...] = (),
) -> ManagedCandidateProfile:
    sp = SearchProfile(
        profile_id=profile_id,
        positive_example_texts=pos,
        negative_example_texts=neg,
        positive_job_example_texts=pos_job,
        negative_job_example_texts=neg_job,
        allowed_languages=allowed_languages,
    )
    return ManagedCandidateProfile(
        user_id=user_id,
        profile_id=profile_id,
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id=user_id, display_name="u1"),
            search_profiles=(sp,),
        ),
        updated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# ResumeExtractionPayload None -> ()
# ---------------------------------------------------------------------------


def test_payload_coerces_none_arrays_to_empty() -> None:
    """The LLM occasionally returns ``null`` for optional list fields.
    Pydantic v2 would normally reject that for ``tuple[str, ...]``;
    our validator must coerce to ``()`` so the rest of the
    pipeline keeps the other (correct) fields instead of throwing
    them away.
    """
    payload = ResumeExtractionPayload.model_validate(
        {
            "name": "Alice",
            "summary": None,
            "target_roles": None,
            "required_skills": None,
            "preferred_skills": None,
            "target_domains": None,
            "anti_preferences": None,
            "preferred_regions": None,
            "preferred_countries": None,
            "preferred_cities": None,
            "languages": None,
            "seniority_hint": None,
            "keywords": None,
        }
    )
    assert payload.name == "Alice"
    assert payload.target_roles == ()
    assert payload.required_skills == ()
    assert payload.languages == ()


def test_payload_preserves_populated_fields() -> None:
    payload = ResumeExtractionPayload.model_validate(
        {
            "name": "Bob",
            "target_roles": ("ML Engineer",),
            "required_skills": ("python", "pytorch"),
        }
    )
    assert payload.target_roles == ("ML Engineer",)
    assert payload.required_skills == ("python", "pytorch")


# ---------------------------------------------------------------------------
# merge_resume_profile: allowed_languages + soft_preferences
# ---------------------------------------------------------------------------


def test_merge_propagates_allowed_languages() -> None:
    """The first /run regression: the extracted profile had
    ``allowed_languages=("en",)`` but the existing profile was
    empty; the merge must take the union, not silently drop the
    field.
    """
    existing = _make_profile(pos=("resume 1",))
    extracted = _make_profile(
        user_id="u1",
        profile_id="user_u1",
        pos=("resume 2",),
        allowed_languages=(LanguageCode.EN,),
    )
    merged = merge_resume_profile(existing, extracted)
    sp = merged.profile.search_profiles[0]
    assert LanguageCode.EN in sp.allowed_languages


def test_merge_combines_languages_across_calls() -> None:
    """Adding a second resume whose language extraction returned
    ``("ru",)`` must NOT lose the first resume's ``("en",)``.
    """
    first_existing = _make_profile(
        pos=("first",),
        allowed_languages=(LanguageCode.EN,),
    )
    second_extracted = _make_profile(
        user_id="u1",
        profile_id="user_u1",
        pos=("second",),
        allowed_languages=(LanguageCode.RU,),
    )
    merged = merge_resume_profile(first_existing, second_extracted)
    sp = merged.profile.search_profiles[0]
    assert LanguageCode.EN in sp.allowed_languages
    assert LanguageCode.RU in sp.allowed_languages


def test_merge_combines_soft_preferences() -> None:
    existing = _make_profile(pos=("a",))
    existing_sp = existing.profile.search_profiles[0]
    existing = existing.model_copy(
        update={
            "profile": existing.profile.model_copy(
                update={
                    "search_profiles": (
                        existing_sp.model_copy(
                            update={
                                "soft_preferences": ("python", "docker"),
                            }
                        ),
                    )
                    + existing.profile.search_profiles[1:],
                }
            ),
        }
    )
    extracted = _make_profile(user_id="u1", profile_id="user_u1", pos=("b",))
    extracted_sp = extracted.profile.search_profiles[0]
    extracted = extracted.model_copy(
        update={
            "profile": extracted.profile.model_copy(
                update={
                    "search_profiles": (
                        extracted_sp.model_copy(
                            update={
                                "soft_preferences": ("kubernetes",),
                            }
                        ),
                    )
                    + extracted.profile.search_profiles[1:],
                }
            ),
        }
    )
    merged = merge_resume_profile(existing, extracted)
    sp = merged.profile.search_profiles[0]
    # Both sides' soft preferences survive.
    assert "python" in sp.soft_preferences
    assert "docker" in sp.soft_preferences
    assert "kubernetes" in sp.soft_preferences


# ---------------------------------------------------------------------------
# HardFilter: permissive by default
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hard_filter_permits_all_languages_when_allowlist_empty() -> None:
    """The bot's text-only /positive path leaves
    ``allowed_languages=()``. A missing allow-list must accept every
    language; otherwise a single bot session can silently block
    every English vacancy.
    """
    catalog = ProfileCatalog(profiles=(SearchProfile(profile_id="p1"),))
    node = HardFilterNode(catalog)
    from job_ftch.domain import PostType, RawItem, SourceKind

    for lang in ("en", "ru", "kk", "unknown"):
        item = RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id=f"x_{lang}",
            text="Senior LLM Engineer role with Python and RAG",
            metadata={
                "preclassified_post_type": PostType.JOB_POSTING.value,
                "detected_language": lang,
            },
        )
        result = await node.process(item)
        assert result is item, f"language {lang!r} must be allowed when allow-list is empty"


@pytest.mark.anyio
async def test_hard_filter_respects_explicit_allowlist() -> None:
    """If the user DID populate the allow-list (e.g. via a PDF
    resume that extracted languages), the filter must honour it.
    """
    from job_ftch.domain import PostType, RawItem, SourceKind

    catalog = ProfileCatalog(
        profiles=(SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,)),)
    )
    node = HardFilterNode(catalog)
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="x_en",
        text="Senior LLM Engineer role with Python and RAG",
        metadata={
            "preclassified_post_type": PostType.JOB_POSTING.value,
            "detected_language": "en",
        },
    )
    result = await node.process(item)
    assert "language_not_allowed:en" in result.metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_unknown_language_always_allowed() -> None:
    """If the detector returns ``unknown`` (short or mixed-language
    post) the filter must not block — the previous ``return not
    allowed or language in allowed or language == "unknown"``
    already did this; the new contract keeps it.
    """
    from job_ftch.domain import PostType, RawItem, SourceKind

    catalog = ProfileCatalog(
        profiles=(SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,)),)
    )
    node = HardFilterNode(catalog)
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="x_unk",
        text="Senior LLM Engineer role",
        metadata={
            "preclassified_post_type": PostType.JOB_POSTING.value,
            "detected_language": "unknown",
        },
    )
    result = await node.process(item)
    assert result is item


# ---------------------------------------------------------------------------
# MultiProfileMatchNode: lenient on language
# ---------------------------------------------------------------------------


def test_match_scoring_unknown_language_passes_hard_constraints() -> None:
    """Even when the user has set an allow-list, ``unknown`` is
    always allowed through the soft match-score gate.
    """
    from job_ftch.domain import JobRecord, LanguageCode, PostType, SourceKind, WorkMode

    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    node = MultiProfileMatchNode(ProfileCatalog(profiles=(profile,)))
    job = JobRecord(
        title="Senior LLM Engineer",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="t",
        raw_item_id="r1",
        post_type=PostType.JOB_POSTING,
        language=LanguageCode.UNKNOWN,  # detected as unknown
        work_mode=WorkMode.REMOTE,
    )
    # No exception means the hard-constraint check passed.
    assert node._passes_hard_constraints(job, profile) is True


def test_match_scoring_blocks_when_allowlist_disallows_language() -> None:
    from job_ftch.domain import JobRecord, LanguageCode, PostType, SourceKind, WorkMode

    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    node = MultiProfileMatchNode(ProfileCatalog(profiles=(profile,)))
    job = JobRecord(
        title="Senior LLM Engineer",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="t",
        raw_item_id="r1",
        post_type=PostType.JOB_POSTING,
        language=LanguageCode.EN,
        work_mode=WorkMode.REMOTE,
    )
    assert node._passes_hard_constraints(job, profile) is False
