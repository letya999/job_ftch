from job_ftch.domain import (
    AssessedJob,
    ClaimAssessment,
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    MatchDecision,
    PostType,
    WorkState,
)
from job_ftch.nodes.decision import DecisionNode


def _assessment(claim, subject, belief, certainty, profile_id=None):
    return ClaimAssessment(
        claim=claim,
        subject=subject,
        profile_id=profile_id,
        belief_true=belief,
        certainty=certainty,
        coverage=certainty,
        conflict=0.0,
        support_mass=belief,
        contradiction_mass=1.0 - belief,
        evidence_ids=(f"{subject}-evidence",),
    )


async def test_accept_requires_jobness_and_one_profile(make_job_record):
    record = make_job_record(post_type=PostType.UNKNOWN)
    item = AssessedJob(
        record=record,
        evidence=(
            EvidenceAtom(
                evidence_id="llm-relevance",
                claim=ClaimKind.PROFILE_RELEVANCE,
                subject="profile_relevance",
                profile_id="p1",
                polarity=EvidencePolarity.SUPPORTS,
                strength=0.95,
                reliability=0.9,
                provenance=EvidenceProvenance.LLM,
                producer="llm_relevance",
                producer_version="1",
                independence_key="llm:p1",
                observation_id=record.raw_item_id,
                candidate_id=record.raw_item_id,
                evidence_ref="test:llm",
            ),
        ),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.9, 0.8, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.work_state is WorkState.TERMINAL
    assert result.assessed_job.record.routing_decision is MatchDecision.ACCEPT
    assert result.assessed_job.record.post_type is PostType.JOB_POSTING


async def test_personal_audit_routes_to_full_extraction(make_job_record):
    node = DecisionNode()
    node.enable_audit_mode()

    result = await node.process(AssessedJob(record=make_job_record()))

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.reasons == ("personal_audit_full_extraction",)
    assert result.assessed_job.record.post_type is PostType.JOB_POSTING


async def test_cheap_positive_profile_evidence_cannot_accept_without_llm(make_job_record):
    item = AssessedJob(
        record=make_job_record(),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.9, 0.8, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.work_state is WorkState.DEFERRED
    assert result.reasons[-1] == "relevance_llm_required"


async def test_non_supporting_llm_evidence_is_review_not_deferred(make_job_record):
    record = make_job_record()
    item = AssessedJob(
        record=record,
        evidence=(
            EvidenceAtom(
                evidence_id="llm-relevance-review",
                claim=ClaimKind.PROFILE_RELEVANCE,
                subject="profile_relevance",
                profile_id="p1",
                polarity=EvidencePolarity.UNKNOWN,
                strength=0.85,
                reliability=0.9,
                provenance=EvidenceProvenance.LLM,
                producer="llm_relevance",
                producer_version="1",
                independence_key="llm:p1",
                observation_id=record.raw_item_id,
                candidate_id=record.raw_item_id,
                evidence_ref="test:llm-review",
            ),
        ),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.9, 0.8, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.REVIEW
    assert result.work_state is WorkState.TERMINAL
    assert result.reasons[-1] == "profile_relevance_uncertain"


async def test_cited_compact_llm_support_is_not_masked_by_lexical_conflict(make_job_record):
    record = make_job_record()
    item = AssessedJob(
        record=record,
        evidence=(
            EvidenceAtom(
                evidence_id="llm-relevance-support",
                claim=ClaimKind.PROFILE_RELEVANCE,
                subject="profile_relevance",
                profile_id="p1",
                polarity=EvidencePolarity.SUPPORTS,
                strength=1.0,
                reliability=0.85,
                provenance=EvidenceProvenance.LLM,
                producer="llm_relevance",
                producer_version="llm-relevance-evidence-v1",
                independence_key="llm:p1",
                observation_id=record.raw_item_id,
                candidate_id=record.raw_item_id,
                evidence_ref="llm:relevance_evidence:1,2",
            ),
        ),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.5, 0.2, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.reasons[-1] == "profile_relevance_confirmed_by_cited_llm"


async def test_strong_uncited_llm_support_confirms_relevance(make_job_record):
    record = make_job_record()
    item = AssessedJob(
        record=record,
        evidence=(
            EvidenceAtom(
                evidence_id="llm-relevance-support",
                claim=ClaimKind.PROFILE_RELEVANCE,
                subject="profile_relevance",
                profile_id="p1",
                polarity=EvidencePolarity.SUPPORTS,
                strength=0.9,
                reliability=0.85,
                provenance=EvidenceProvenance.LLM,
                producer="llm_relevance",
                producer_version="llm-relevance-v2",
                independence_key="llm:p1",
                observation_id=record.raw_item_id,
                candidate_id=record.raw_item_id,
                evidence_ref="llm:relevance_classification",
            ),
        ),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.5, 0.2, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.ACCEPT
    assert result.reasons[-1] == "profile_relevance_confirmed_by_strong_llm"


async def test_llm_contradiction_blocks_strong_uncited_support(make_job_record):
    record = make_job_record()
    base = {
        "claim": ClaimKind.PROFILE_RELEVANCE,
        "subject": "profile_relevance",
        "profile_id": "p1",
        "reliability": 0.85,
        "provenance": EvidenceProvenance.LLM,
        "producer": "llm_relevance",
        "producer_version": "llm-relevance-v2",
        "independence_key": "llm:p1",
        "observation_id": record.raw_item_id,
        "candidate_id": record.raw_item_id,
    }
    item = AssessedJob(
        record=record,
        evidence=(
            EvidenceAtom(
                **base,
                evidence_id="llm-relevance-support",
                polarity=EvidencePolarity.SUPPORTS,
                strength=0.9,
                evidence_ref="llm:relevance_classification",
            ),
            EvidenceAtom(
                **base,
                evidence_id="llm-relevance-contradiction",
                polarity=EvidencePolarity.CONTRADICTS,
                strength=0.9,
                evidence_ref="llm:relevance_evidence:3",
            ),
        ),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.5, 0.2, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.REVIEW


async def test_unknown_jobness_is_deferred_not_rejected(make_job_record):
    item = AssessedJob(
        record=make_job_record(),
        assessments=(
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.95, 0.9, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is None
    assert result.work_state is WorkState.DEFERRED


async def test_all_confident_profile_rejections_are_rejected(make_job_record):
    item = AssessedJob(
        record=make_job_record(),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.1, 0.9, "p1"),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.2, 0.9, "p2"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.routing_decision is MatchDecision.REJECT


async def test_degradation_is_deferred_even_with_other_positive_evidence(make_job_record):
    item = AssessedJob(
        record=make_job_record(),
        degradation_reasons=("evidence_producer_failed:llm",),
        assessments=(
            _assessment(ClaimKind.IS_JOB, "vacancy", 0.95, 0.9),
            _assessment(ClaimKind.PROFILE_RELEVANCE, "profile_relevance", 0.95, 0.9, "p1"),
        ),
    )

    result = await DecisionNode().process(item)

    assert result.work_state is WorkState.DEFERRED
    assert result.routing_decision is None
