import pytest

from job_ftch.domain import JobReviewReason, MatchDecision, ProfileMatchScore
from job_ftch.nodes.routing import RoutingNode


@pytest.fixture
def routing_node():
    return RoutingNode(accept_threshold=0.8, review_threshold=0.4, quality_override_threshold=0.6)


@pytest.mark.anyio
async def test_routing_no_profile_scores_rejects(routing_node, make_job_record):
    job = make_job_record(profile_scores=())
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.REJECT
    assert "no_profile_match" in processed.review_reasons


@pytest.mark.anyio
async def test_routing_high_score_accepts(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.9)
    job = make_job_record(profile_scores=(score,), quality_score=0.8)
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.ACCEPT


@pytest.mark.anyio
async def test_routing_mid_score_reviews(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.5)
    job = make_job_record(profile_scores=(score,))
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.REVIEW


@pytest.mark.anyio
async def test_routing_low_score_rejects(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.3)
    job = make_job_record(profile_scores=(score,))
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.REJECT


@pytest.mark.anyio
async def test_routing_quality_override_demotes_accept_to_review(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.9)
    job = make_job_record(profile_scores=(score,), quality_score=0.4)  # < 0.6
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.REVIEW
    assert JobReviewReason.LOW_QUALITY_SCORE.value in processed.review_reasons


@pytest.mark.anyio
async def test_routing_quality_override_does_not_affect_review(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.5)
    job = make_job_record(profile_scores=(score,), quality_score=0.4)
    processed = await routing_node.process(job)
    assert processed.routing_decision == MatchDecision.REVIEW


@pytest.mark.anyio
async def test_routing_preserves_existing_review_reasons(routing_node, make_job_record):
    score = ProfileMatchScore(profile_id="p1", profile_name="P1", final_score=0.9)
    job = make_job_record(profile_scores=(score,), review_reasons=("existing",))
    processed = await routing_node.process(job)
    assert "existing" in processed.review_reasons
