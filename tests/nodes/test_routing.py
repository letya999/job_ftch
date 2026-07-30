"""Historical RoutingNode remains executable for the baseline preset."""

import pytest

from job_ftch.domain import JobReviewReason, MatchDecision
from job_ftch.nodes.routing import RoutingNode


@pytest.mark.asyncio
async def test_historical_routing_rejects_without_a_relevance_signal(make_job_record) -> None:
    item = make_job_record()
    out = await RoutingNode().process(item)
    assert out.routing_decision.value == "reject"
    assert "profile_reject" in out.review_reasons


@pytest.mark.asyncio
async def test_llm_relevance_accept(make_job_record) -> None:
    item = make_job_record(metadata={"_llm_relevance": {"decision": "accept", "confidence": 0.95}})
    out = await RoutingNode().process(item)
    assert out.routing_decision is MatchDecision.ACCEPT
    assert "llm_relevance:accept" in out.review_reasons
    assert out.relevance_score == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_llm_relevance_reject(make_job_record) -> None:
    item = make_job_record(metadata={"_llm_relevance": {"decision": "reject"}})
    out = await RoutingNode().process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert "llm_relevance:reject" in out.review_reasons


@pytest.mark.asyncio
async def test_llm_relevance_preserves_higher_existing_score(make_job_record) -> None:
    item = make_job_record(
        relevance_score=0.99,
        metadata={"_llm_relevance": {"decision": "accept", "confidence": 0.80}},
    )
    out = await RoutingNode().process(item)
    assert out.relevance_score == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_uncertainty_recommendation_accept(make_job_record) -> None:
    item = make_job_record(metadata={"uncertainty_recommendation": "accept"})
    out = await RoutingNode().process(item)
    assert out.routing_decision is MatchDecision.ACCEPT
    assert "uncertainty_router:accept" in out.review_reasons


@pytest.mark.asyncio
async def test_uncertainty_recommendation_reject(make_job_record) -> None:
    item = make_job_record(metadata={"uncertainty_recommendation": "reject"})
    out = await RoutingNode().process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert "uncertainty_router:reject" in out.review_reasons


@pytest.mark.asyncio
async def test_parallel_score_above_accept_threshold(make_job_record) -> None:
    node = RoutingNode(accept_threshold=0.55)
    item = make_job_record(metadata={"parallel_final_score": 0.60}, quality_score=0.8)
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.ACCEPT
    assert "profile_match" in out.review_reasons


@pytest.mark.asyncio
async def test_parallel_score_between_review_and_accept(make_job_record) -> None:
    node = RoutingNode(accept_threshold=0.55, review_threshold=0.5)
    item = make_job_record(metadata={"parallel_final_score": 0.52}, quality_score=0.8)
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert "profile_review" in out.review_reasons


@pytest.mark.asyncio
async def test_quality_override_demotes_accept_to_reject(make_job_record) -> None:
    node = RoutingNode(accept_threshold=0.55, quality_override_threshold=0.6)
    item = make_job_record(
        metadata={"parallel_final_score": 0.70},
        quality_score=0.4,
    )
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert JobReviewReason.LOW_QUALITY_SCORE.value in out.review_reasons


@pytest.mark.asyncio
async def test_quality_above_threshold_keeps_accept(make_job_record) -> None:
    node = RoutingNode(accept_threshold=0.55, quality_override_threshold=0.6)
    item = make_job_record(
        metadata={"parallel_final_score": 0.70},
        quality_score=0.8,
    )
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.ACCEPT


@pytest.mark.asyncio
async def test_configure_graph_params_overrides_thresholds(make_job_record) -> None:
    node = RoutingNode(accept_threshold=0.55)
    node.configure_graph_params({"accept_threshold": 0.90})
    item = make_job_record(
        metadata={"parallel_final_score": 0.70},
        quality_score=0.8,
    )
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.REJECT


@pytest.mark.asyncio
async def test_configure_graph_policy_weighted_mode(make_job_record) -> None:
    node = RoutingNode()
    node.configure_graph_policy(
        {
            "mode": "weighted",
            "threshold": 0.5,
            "signals": [{"name": "parallel_final_score", "weight": 1.0, "default": 0.0}],
        }
    )
    item = make_job_record(
        metadata={"parallel_final_score": 0.80},
        quality_score=0.8,
    )
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.ACCEPT
    assert any("policy_score:" in r for r in out.review_reasons)


@pytest.mark.asyncio
async def test_configure_graph_policy_invalid_mode_falls_back_to_legacy(make_job_record) -> None:
    node = RoutingNode()
    node.configure_graph_policy({"mode": "unknown_mode"})
    item = make_job_record()
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert "profile_reject" in out.review_reasons


@pytest.mark.asyncio
async def test_weighted_policy_veto(make_job_record) -> None:
    node = RoutingNode()
    node.configure_graph_policy(
        {
            "mode": "weighted",
            "threshold": 0.5,
            "signals": [{"name": "parallel_final_score", "weight": 1.0, "default": 0.0}],
            "vetoes": [{"name": "quality_score", "lt": 0.5}],
        }
    )
    item = make_job_record(
        metadata={"parallel_final_score": 0.90, "quality_score": 0.3},
        quality_score=0.3,
    )
    out = await node.process(item)
    assert out.routing_decision is MatchDecision.REJECT
    assert any("veto:quality_score" in r for r in out.review_reasons)
