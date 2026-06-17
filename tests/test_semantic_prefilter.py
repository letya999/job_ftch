from __future__ import annotations

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import ProfileCatalog, RawItem, SourceKind, TriageRejectionReason
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode


@pytest.mark.asyncio
async def test_semantic_prefilter_keeps_obvious_ai_role() -> None:
    node = SemanticPrefilterNode(ProfileCatalog.default())
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.DEBUG,
            "source_name": "debug-feed",
            "external_id": "fixture-001",
            "url": "https://example.com/jobs/fixture-001",
            "text": "Senior AI Engineer at Example AI. Remote in Europe. Requires python and pytorch skills.",
        }
    )

    enriched = await node.process(item)

    assert enriched is not None
    assert "semantic_prefilter_best_score" in enriched.metadata


@pytest.mark.asyncio
async def test_semantic_prefilter_drops_clear_noise() -> None:
    node = SemanticPrefilterNode(ProfileCatalog.default())
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.DEBUG,
            "source_name": "debug-feed",
            "external_id": "fixture-002",
            "url": "https://example.com/noise/fixture-002",
            "text": "Weekly webinar digest for marketers and sales teams.",
        }
    )

    with pytest.raises(RawItemDropped) as exc_info:
        await node.process(item)

    assert exc_info.value.reason == TriageRejectionReason.LOW_RELEVANCE_PREFILTER


@pytest.mark.asyncio
async def test_semantic_prefilter_bypasses_career_site_items() -> None:
    node = SemanticPrefilterNode(ProfileCatalog.default())
    item = RawItem.model_validate(
        {
            "source_kind": SourceKind.CAREER_SITE,
            "source_name": "dom",
            "external_id": "fixture-career-001",
            "url": "https://example.com/careers/job-1",
            "text": "Senior Frontend Developer",
        }
    )

    enriched = await node.process(item)

    assert enriched is item


@pytest.mark.asyncio
async def test_semantic_prefilter_skill_fallback(minimal_profile, make_raw_item) -> None:
    # Profile with required_skills but empty soft_preferences
    profile = minimal_profile.model_copy(update={"soft_preferences": ()})
    node = SemanticPrefilterNode(ProfileCatalog(profiles=[profile]))

    # Text contains "python" (required skill canonical name)
    item = make_raw_item(text="Looking for a specialist in python")

    # Should pass prefilter because it falls back to required_skills for soft_score
    # Increase threshold ratio to ensure it passes
    node._uncertain_ratio = 0.5
    enriched = await node.process(item)
    assert enriched is not None
    assert float(enriched.metadata["semantic_prefilter_best_score"]) > 0


@pytest.mark.asyncio
async def test_semantic_prefilter_description_bonus_threshold(
    minimal_profile, make_raw_item
) -> None:
    profile = minimal_profile.model_copy(
        update={"profile_description": "specialist in building large language models and agents"}
    )
    node = SemanticPrefilterNode(ProfileCatalog(profiles=[profile]))
    node._uncertain_ratio = 0.0  # bypass drop logic for score comparison

    # item_no_bonus: no words from profile_description appear in text
    item_no_bonus = make_raw_item(text="secretary job at city hall")
    enriched_no = await node.process(item_no_bonus)
    score_no = float(enriched_no.metadata["semantic_prefilter_best_score"])

    # item_bonus: contains "language" which is in profile_description and >= 2 chars, not a stop-word
    item_bonus = make_raw_item(text="building a language tool")
    enriched_yes = await node.process(item_bonus)
    score_yes = float(enriched_yes.metadata["semantic_prefilter_best_score"])

    # The desc bonus adds 0.15 when any profile_description token appears in the item text.
    assert score_yes > score_no
    assert score_yes - score_no == pytest.approx(0.15, abs=0.01)
