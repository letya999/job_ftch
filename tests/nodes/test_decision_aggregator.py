import pytest

from job_ftch.domain import MatchDecision
from job_ftch.nodes.decision_aggregator import DecisionAggregatorNode


@pytest.mark.asyncio
async def test_missing_llm_evidence_routes_to_review_when_rescue_is_disabled(
    make_job_record,
) -> None:
    item = make_job_record(
        best_score=0.9,
        metadata={"parallel_final_score": 0.9},
    )

    result = await DecisionAggregatorNode(allow_missing_llm_rescue=False).process(item)

    assert result.routing_decision is MatchDecision.REVIEW
    assert "decision_aggregator:missing_llm_evidence" in result.review_reasons


@pytest.mark.asyncio
async def test_llm_accept_with_profile_anti_evidence_routes_to_review(make_job_record) -> None:
    item = make_job_record(
        metadata={
            "_llm_relevance": {"decision": "accept", "confidence": 0.9},
            "lexical_negative_matches": ("sales",),
            "profile_semantic_margin": 0.2,
        },
    )

    result = await DecisionAggregatorNode(require_no_profile_conflict=True).process(item)

    assert result.routing_decision is MatchDecision.REVIEW
    assert "decision_aggregator:llm_accept_profile_conflict" in result.review_reasons


@pytest.mark.asyncio
async def test_llm_reject_is_terminal_when_rescue_is_disabled(make_job_record) -> None:
    item = make_job_record(metadata={"_llm_relevance": {"decision": "reject", "confidence": 0.9}})

    result = await DecisionAggregatorNode(allow_reject_rescue=False).process(item)

    assert result.routing_decision is MatchDecision.REJECT


def test_combined_core_schema_ignores_surplus_llm_fields() -> None:
    from job_ftch.nodes.decision_extraction import CoreExtractedDecisionFields

    fields = CoreExtractedDecisionFields.model_validate(
        {
            "title": "AI Engineer",
            "decision": "review",
            "confidence": 0.4,
            "reasoning": "Mixed signals",
            "benefits": [],
            "culture": [],
        }
    )

    assert fields.title == "AI Engineer"
    assert fields.decision == "review"
