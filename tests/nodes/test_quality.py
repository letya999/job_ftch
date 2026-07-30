import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    JobReviewReason,
    JobValidationRejectionReason,
    MatchDecision,
    ProfileMatchScore,
    SkillTag,
    WorkMode,
)
from job_ftch.nodes.quality import JobValidationNode, QualityScoringNode


@pytest.mark.anyio
async def test_quality_scoring_full_data_scores_high(make_job_record):
    node = QualityScoringNode()
    job = make_job_record(
        title="Senior ML",
        company="OpenAI",
        canonical_url="https://openai.com/job1",
        location="SF",
        work_mode=WorkMode.REMOTE,
        skills_explicit=(SkillTag(canonical_name="python"),),
        description="A very long description that should meet the length requirement for higher score. "
        * 5,
    )
    processed = await node.process(job)
    assert processed.quality_score >= 0.9


@pytest.mark.anyio
async def test_quality_scoring_empty_job_scores_low(make_job_record):
    node = QualityScoringNode()
    job = make_job_record(title=None, company=None, description="short")
    processed = await node.process(job)
    assert processed.quality_score < 0.4


@pytest.mark.anyio
async def test_quality_scoring_risk_signals_penalize(make_job_record):
    node = QualityScoringNode()
    job_clean = make_job_record(title="ML")
    job_risky = make_job_record(title="ML", risk_signals=("signal1", "signal2"))

    res_clean = await node.process(job_clean)
    res_risky = await node.process(job_risky)

    assert res_risky.quality_score < res_clean.quality_score


@pytest.mark.anyio
async def test_quality_is_independent_from_relevance_score(make_job_record):
    node = QualityScoringNode()
    low_relevance = make_job_record(relevance_score=0.01)
    high_relevance = make_job_record(relevance_score=0.99)

    low = await node.process(low_relevance)
    high = await node.process(high_relevance)

    assert low.quality_score == high.quality_score


@pytest.mark.anyio
async def test_quality_scoring_adds_review_reason_when_below_threshold(make_job_record):
    node = QualityScoringNode(review_threshold=0.6)
    job = make_job_record(title=None, company=None, description="short")
    processed = await node.process(job)
    assert JobReviewReason.LOW_QUALITY_SCORE.value in processed.review_reasons


@pytest.mark.anyio
async def test_job_validation_drops_missing_core_fields(make_job_record):
    node = JobValidationNode()
    job = make_job_record(title=None, company=None, canonical_url=None)
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job)
    assert exc.value.reason == JobValidationRejectionReason.JOB_MISSING_CORE_FIELDS


@pytest.mark.anyio
async def test_job_validation_drops_low_relevance(make_job_record):
    node = JobValidationNode(min_relevance_score=0.5)
    job = make_job_record(title="ML", relevance_score=0.3)
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE


@pytest.mark.anyio
async def test_job_validation_drops_all_profiles_rejected(make_job_record):
    node = JobValidationNode()
    score = ProfileMatchScore(
        profile_id="p1", profile_name="P1", decision=MatchDecision.REJECT, final_score=0.1
    )
    job = make_job_record(title="ML", profile_scores=(score,))
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE


@pytest.mark.anyio
async def test_job_validation_drops_low_quality_score(make_job_record):
    node = JobValidationNode(min_quality_score=0.5)
    job = make_job_record(title="ML", quality_score=0.3)
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job)
    assert exc.value.reason == JobValidationRejectionReason.JOB_TOO_LOW_QUALITY


@pytest.mark.anyio
async def test_job_validation_passes_valid_job(make_job_record):
    node = JobValidationNode(min_quality_score=0.1, min_relevance_score=0.1)
    job = make_job_record(title="ML", company="Co", relevance_score=0.5, quality_score=0.5)
    assert await node.process(job) is job
