import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import FilterProfile, Job, JobValidationRejectionReason, SourceKind
from job_ftch.nodes.relevance import AIRoleRelevanceNode


@pytest.fixture
def custom_profile():
    return FilterProfile(
        positive_relevance_keywords=["ml", "nlp"],
        negative_relevance_keywords=["sales"],
        relevance_threshold=0.1,
    )


@pytest.mark.anyio
async def test_relevance_scores_matching_role_family(custom_profile):
    node = AIRoleRelevanceNode(profile=custom_profile)
    job = Job(
        title="ML Engineer",
        description="Build NLP models",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
    )
    processed = await node.process(job)
    assert processed.relevance_score > 0.5  # 2 hits -> 2/3 -> 0.66


@pytest.mark.anyio
async def test_relevance_low_score_for_unrelated_job(custom_profile):
    node = AIRoleRelevanceNode(profile=custom_profile)
    job = Job(
        title="Accountant",
        description="Numbers",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE


@pytest.mark.anyio
async def test_relevance_uses_fallback_heuristic(custom_profile):
    node = AIRoleRelevanceNode(profile=custom_profile)
    job = Job(
        title="Junior ML",
        description="test",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
    )
    processed = await node.process(job)
    assert processed.relevance_score == pytest.approx(0.33, abs=0.01)


@pytest.mark.anyio
async def test_relevance_updates_relevance_score_field(custom_profile):
    node = AIRoleRelevanceNode(profile=custom_profile)
    job = Job(
        title="ML Engineer",
        description="test",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
    )
    processed = await node.process(job)
    assert processed.relevance_score is not None
