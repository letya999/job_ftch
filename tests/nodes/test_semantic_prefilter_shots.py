"""Shot-anchor relevance branch of SemanticPrefilterNode.

When a relevance scorer is injected, the node decides on the embedding margin
instead of YAML token overlap, and applies to every source kind (no career-site
bypass, since semantic embeddings handle career postings).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.profile import ProfileCatalog
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode


@dataclass
class _Score:
    margin: float


class _StubScorer:
    def __init__(self, margin: float) -> None:
        self._margin = margin

    def score_text(self, text: str) -> _Score:  # noqa: ARG002 - fixed stub
        return _Score(self._margin)


def _item(
    text: str = "ML Engineer, PyTorch, LLM", kind: SourceKind = SourceKind.TELEGRAM_CHANNEL
) -> RawItem:
    return RawItem(
        source_kind=kind,
        source_name="t",
        external_id="1",
        text=text,
    )


@pytest.mark.asyncio
async def test_shot_scorer_passes_when_margin_above_threshold() -> None:
    node = SemanticPrefilterNode(
        ProfileCatalog(profiles=[]),
        relevance_scorer=_StubScorer(0.10),
        relevance_threshold=0.0,
    )
    out = await node.process(_item())
    assert out is not None
    assert out.metadata["semantic_prefilter_shot_margin"] == "0.1000"


@pytest.mark.asyncio
async def test_shot_scorer_marks_when_margin_below_threshold() -> None:
    node = SemanticPrefilterNode(
        ProfileCatalog(profiles=[]),
        relevance_scorer=_StubScorer(-0.05),
        relevance_threshold=0.0,
    )
    assert (await node.process(_item("привет всем, как дела"))).metadata[
        "semantic_prefilter_uncertain"
    ] is True


@pytest.mark.asyncio
async def test_shot_scorer_keeps_explicit_llm_agent_job_under_threshold() -> None:
    node = SemanticPrefilterNode(
        ProfileCatalog(profiles=[]),
        relevance_scorer=_StubScorer(0.02),
        relevance_threshold=0.05,
    )
    out = await node.process(
        _item(
            "Python Developer: разработка LLM-агентов, RAG, MCP-серверов "
            "и orchestration через LangGraph."
        )
    )
    assert out is not None
    assert out.metadata["semantic_prefilter_override"] == "strong_ai_signal"
    assert out.metadata["semantic_prefilter_shot_margin"] == "0.0200"


@pytest.mark.asyncio
async def test_shot_scorer_applies_to_career_site_too() -> None:
    # With token overlap, career_site is bypassed; with shots it is scored.
    node = SemanticPrefilterNode(
        ProfileCatalog(profiles=[]),
        relevance_scorer=_StubScorer(-0.2),
        relevance_threshold=0.0,
    )
    assert (await node.process(_item("бухгалтер 1С", kind=SourceKind.CAREER_SITE))).metadata[
        "semantic_prefilter_uncertain"
    ] is True
