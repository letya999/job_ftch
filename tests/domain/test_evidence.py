from datetime import UTC, datetime, timedelta

from job_ftch.domain import (
    ClaimKind,
    ClaimParameters,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    SourceFamily,
    aggregate_claim,
)


def _atom(
    evidence_id: str,
    *,
    polarity: EvidencePolarity = EvidencePolarity.SUPPORTS,
    independence_key: str | None = None,
    strength: float = 1.0,
    reliability: float = 1.0,
    expires_at: datetime | None = None,
) -> EvidenceAtom:
    return EvidenceAtom(
        evidence_id=evidence_id,
        claim=ClaimKind.IS_JOB,
        subject="vacancy",
        polarity=polarity,
        strength=strength,
        reliability=reliability,
        provenance=EvidenceProvenance.CLASSIFIER,
        producer="test",
        producer_version="1",
        source_family=SourceFamily.TELEGRAM,
        independence_key=independence_key or evidence_id,
        observation_id="observation-1",
        candidate_id="candidate-1",
        evidence_ref=f"span:{evidence_id}",
        expires_at=expires_at,
    )


def test_independent_support_increases_certainty_with_diminishing_returns() -> None:
    one = aggregate_claim([_atom("one")])
    two = aggregate_claim([_atom("one"), _atom("two")])
    three = aggregate_claim([_atom("one"), _atom("two"), _atom("three")])

    assert one.certainty < two.certainty < three.certainty
    assert (two.certainty - one.certainty) > (three.certainty - two.certainty)


def test_same_independence_group_is_not_counted_twice() -> None:
    one = aggregate_claim([_atom("one")])
    duplicate = aggregate_claim([_atom("one"), _atom("same-span-model-2", independence_key="one")])

    assert duplicate.support_mass == one.support_mass
    assert duplicate.certainty == one.certainty


def test_contradiction_changes_belief_and_conflict_reduces_certainty() -> None:
    supported = aggregate_claim([_atom("support")])
    conflicted = aggregate_claim(
        [_atom("support"), _atom("contra", polarity=EvidencePolarity.CONTRADICTS)]
    )

    assert conflicted.belief_true < supported.belief_true
    assert conflicted.conflict > 0
    assert conflicted.certainty < supported.certainty


def test_unknown_observation_is_auditable_but_has_no_claim_mass() -> None:
    assessment = aggregate_claim([_atom("review", polarity=EvidencePolarity.UNKNOWN)])

    assert assessment.belief_true == 0.5
    assert assessment.certainty == 0.0
    assert assessment.support_mass == 0.0
    assert assessment.contradiction_mass == 0.0
    assert assessment.evidence_ids == ("review",)


def test_expired_evidence_does_not_increase_confidence() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    active = aggregate_claim([_atom("active")], now=now)
    expired = aggregate_claim(
        [_atom("expired", expires_at=now - timedelta(seconds=1))],
        now=now,
    )

    assert expired.coverage == 0
    assert expired.certainty == 0
    assert active.certainty > expired.certainty


def test_parameters_are_explicit_and_bound_confidence() -> None:
    result = aggregate_claim(
        [_atom("weak", strength=0.2, reliability=0.5)],
        ClaimParameters(coverage_rate=0.5, support_weight=0.5),
    )

    assert 0 < result.certainty < 1
    assert 0 < result.belief_true < 1
