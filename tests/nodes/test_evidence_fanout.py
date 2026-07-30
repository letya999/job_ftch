import asyncio

from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    SourceFamily,
)
from job_ftch.nodes.evidence_fanout import EvidenceFanOutNode, ProfileLexicalEvidenceProducer


def _atom(item, value: str) -> EvidenceAtom:
    return EvidenceAtom(
        evidence_id=f"{item.raw_item_id}:{value}",
        claim=ClaimKind.IS_JOB,
        subject="vacancy",
        polarity=EvidencePolarity.SUPPORTS,
        strength=1.0,
        reliability=1.0,
        provenance=EvidenceProvenance.CLASSIFIER,
        producer="test",
        producer_version="1",
        source_family=SourceFamily.FIXTURE,
        independence_key=f"{item.raw_item_id}:{value}",
        observation_id=item.raw_item_id,
        candidate_id=item.raw_item_id,
        evidence_ref=value,
    )


class _Producer:
    def __init__(self, value: str, *, delay: float = 0.0, fail: bool = False) -> None:
        self.value = value
        self.delay = delay
        self.fail = fail

    async def produce(self, item):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider down")
        return (_atom(item, self.value),)


async def test_fanout_is_order_independent_and_aggregates_all_producers(make_job_record):
    item = make_job_record(raw_item_id="r1")
    node = EvidenceFanOutNode(
        [_Producer("slow", delay=0.02), _Producer("fast", delay=0.0)],
        timeout_seconds=1.0,
    )

    result = await node.process(item)

    assert [atom.evidence_id for atom in result.evidence] == ["r1:fast", "r1:slow"]
    assert result.assessments[0].certainty > 0
    assert result.degradation_reasons == ()


async def test_failed_branch_is_degraded_without_losing_other_evidence(make_job_record):
    item = make_job_record(raw_item_id="r2")
    node = EvidenceFanOutNode([_Producer("ok"), _Producer("bad", fail=True)])

    result = await node.process(item)

    assert len(result.evidence) == 1
    assert result.degradation_reasons == ("evidence_producer_failed:_Producer",)


async def test_fanout_deadline_degrades_without_waiting_for_slow_provider(make_job_record):
    item = make_job_record(raw_item_id="r3")
    node = EvidenceFanOutNode([_Producer("slow", delay=0.2)], timeout_seconds=0.01)

    result = await node.process(item)

    assert result.evidence == ()
    assert result.degradation_reasons == ("evidence_fanout_timeout",)


async def test_profile_lexical_negative_match_is_typed_contradiction(make_job_record):
    item = make_job_record(
        raw_item_id="r4",
        metadata={
            "lexical_profile_matches": {
                "default": {"positive": (), "negative": ("mlops",)},
            }
        },
    )

    atoms = await ProfileLexicalEvidenceProducer().produce(item)

    assert len(atoms) == 1
    assert atoms[0].claim is ClaimKind.PROFILE_RELEVANCE
    assert atoms[0].polarity is EvidencePolarity.CONTRADICTS
    assert atoms[0].profile_id == "default"
