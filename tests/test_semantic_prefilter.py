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
            "text": "Senior AI Engineer at Example AI. Remote in Europe.",
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

    assert exc_info.value.reason == TriageRejectionReason.TELEGRAM_LOW_SIGNAL
