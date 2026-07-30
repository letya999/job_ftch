from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    JobRecord,
    SourceFamily,
)
from job_ftch.nodes.decision import DecisionNode
from job_ftch.nodes.evidence_decision import EvidenceDecisionNode
from job_ftch.nodes.evidence_fanout import EvidenceFanOutNode


class _FixedProducer:
    async def produce(self, item: JobRecord):
        return (
            EvidenceAtom(
                evidence_id=f"{item.raw_item_id}:job",
                claim=ClaimKind.IS_JOB,
                subject="vacancy",
                polarity=EvidencePolarity.SUPPORTS,
                strength=1.0,
                reliability=1.0,
                provenance=EvidenceProvenance.CLASSIFIER,
                producer="test",
                producer_version="1",
                source_family=SourceFamily.FIXTURE,
                independence_key=f"{item.raw_item_id}:job",
                observation_id=item.raw_item_id,
                candidate_id=item.raw_item_id,
                evidence_ref="fixture:job",
            ),
        )


async def test_bridge_keeps_decision_in_one_policy_owner(make_job_record):
    node = EvidenceDecisionNode(
        EvidenceFanOutNode([_FixedProducer()]),
        DecisionNode(),
    )

    result = await node.process(make_job_record())

    assert result.routing_decision.value == "review"
    assert result.metadata["work_state"] == "terminal"
    assert "profile_relevance_unconfigured" in result.metadata["decision_reasons"]
