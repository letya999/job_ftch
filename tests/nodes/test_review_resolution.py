from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from job_ftch.domain import MatchDecision
from job_ftch.nodes.review_resolution import ReviewResolution, ReviewResolutionNode


def _review_item(make_job_record):
    return make_job_record(
        routing_decision=MatchDecision.REVIEW,
        metadata={"_llm_relevance": {"decision": "review", "reasoning": "uncertain"}},
        review_reasons=("primary:review",),
    )


@pytest.mark.asyncio
async def test_non_review_item_is_not_sent_to_llm(make_job_record) -> None:
    llm = AsyncMock()
    item = make_job_record(routing_decision=MatchDecision.REJECT)

    result = await ReviewResolutionNode(llm).process(item)

    assert result is item
    llm.extract.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "confidence", "expected"),
    [
        ("accept", 0.90, MatchDecision.ACCEPT),
        ("reject", 0.90, MatchDecision.REJECT),
        ("review", 0.60, MatchDecision.REVIEW),
    ],
)
async def test_resolution_maps_model_decision(
    make_job_record, decision: str, confidence: float, expected: MatchDecision
) -> None:
    llm = AsyncMock()
    llm.extract.return_value = ReviewResolution(
        decision=decision, confidence=confidence, reasoning="clear evidence"
    )
    result = await ReviewResolutionNode(llm).process(_review_item(make_job_record))

    assert result.routing_decision is expected
    assert f"review_resolution:{decision}" in result.review_reasons
    assert result.metadata["review_resolution"]["final_decision"] == expected.value


@pytest.mark.asyncio
async def test_accept_below_confidence_stays_review(make_job_record) -> None:
    llm = AsyncMock()
    llm.extract.return_value = ReviewResolution(
        decision="accept", confidence=0.69, reasoning="not enough"
    )

    result = await ReviewResolutionNode(llm).process(_review_item(make_job_record))

    assert result.routing_decision is MatchDecision.REVIEW


@pytest.mark.asyncio
async def test_llm_failure_is_recorded(make_job_record) -> None:
    llm = AsyncMock()
    llm.extract.side_effect = RuntimeError("provider down")

    result = await ReviewResolutionNode(llm).process(_review_item(make_job_record))

    assert result.routing_decision is MatchDecision.REVIEW
    assert result.metadata["review_resolution"]["outcome"] == "failed"
    assert result.metadata["review_resolution"]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_max_calls_and_graph_params_bound_calls(make_job_record) -> None:
    llm = AsyncMock()
    llm.extract.return_value = ReviewResolution(
        decision="reject", confidence=1.0, reasoning="clear mismatch"
    )
    node = ReviewResolutionNode(llm, max_calls=1)
    node.configure_graph_params({"max_calls": 1, "accept_confidence": 0.95})

    first = await node.process(_review_item(make_job_record))
    second = await node.process(_review_item(make_job_record))

    assert first.routing_decision is MatchDecision.REJECT
    assert second.routing_decision is MatchDecision.REVIEW
    assert llm.extract.await_count == 1
