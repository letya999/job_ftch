import pytest

from job_ftch.domain import AssessedJob, ClaimAssessment, ClaimKind, JobRecord
from job_ftch.nodes.need_more_evidence import NeedMoreEvidenceNode


@pytest.mark.asyncio
async def test_selects_lowest_certainty_critical_claim_without_routing() -> None:
    record = JobRecord(
        source_record_id="obs", raw_item_id="obs", source_kind="debug", source_name="x"
    )
    item = AssessedJob(
        record=record,
        assessments=(
            ClaimAssessment(
                claim=ClaimKind.PROFILE_RELEVANCE,
                subject="profile_relevance",
                profile_id="p1",
                belief_true=0.5,
                certainty=0.2,
                coverage=0.2,
                conflict=0.0,
                support_mass=0.2,
                contradiction_mass=0.0,
                evidence_ids=("e1",),
            ),
        ),
    )
    out = await NeedMoreEvidenceNode().process(item)
    assert out.record.routing_decision is None
    assert out.record.metadata["missing_critical_claim"] == "profile_relevance:profile_relevance"
