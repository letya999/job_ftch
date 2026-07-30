"""Typed evidence and confidence aggregation for policy decisions."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from job_ftch.domain.models import EvidenceProvenance  # noqa: TC001
from job_ftch.domain.source_identity import (
    AcquisitionTransport,
    ObservationKind,
    SourceFamily,
)


class ClaimKind(StrEnum):
    IS_JOB = "is_job"
    FIELD_VALID = "field_valid"
    PROFILE_RELEVANCE = "profile_relevance"
    HARD_CONSTRAINT = "hard_constraint"
    FRESHNESS = "freshness"
    RISK = "risk"
    EVIDENCE_QUALITY = "evidence_quality"
    DUPLICATE_IDENTITY = "duplicate_identity"


class EvidencePolarity(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    UNKNOWN = "unknown"


class EvidenceAtom(BaseModel):
    """One auditable signal about one claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    claim: ClaimKind
    subject: str = Field(min_length=1)
    profile_id: str | None = None
    polarity: EvidencePolarity
    strength: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    provenance: EvidenceProvenance
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    source_family: SourceFamily = SourceFamily.UNKNOWN
    observation_kind: ObservationKind = ObservationKind.UNKNOWN
    transport: AcquisitionTransport = AcquisitionTransport.UNKNOWN
    independence_key: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None


class ClaimParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prior: float = Field(default=0.5, gt=0.0, lt=1.0)
    support_weight: float = Field(default=1.0, ge=0.0)
    contradiction_weight: float = Field(default=1.0, ge=0.0)
    # A single strong, independently observed signal should be actionable;
    # additional independent signals still increase certainty with diminishing
    # returns through the exponential coverage curve.
    coverage_rate: float = Field(default=1.6, gt=0.0)
    conflict_penalty: float = Field(default=0.75, ge=0.0, le=1.0)
    max_group_mass: float = Field(default=1.0, gt=0.0)


class ClaimAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: ClaimKind
    subject: str
    profile_id: str | None = None
    belief_true: float = Field(ge=0.0, le=1.0)
    certainty: float = Field(ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    conflict: float = Field(ge=0.0, le=1.0)
    support_mass: float = Field(ge=0.0)
    contradiction_mass: float = Field(ge=0.0)
    evidence_ids: tuple[str, ...] = ()


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    atoms: tuple[EvidenceAtom, ...] = ()
    assessments: tuple[ClaimAssessment, ...] = ()
    policy_version: str = Field(default="evidence-v1", min_length=1)


def _freshness_factor(atom: EvidenceAtom, now: datetime) -> float:
    return 0.0 if atom.expires_at is not None and atom.expires_at <= now else 1.0


def _logit(value: float) -> float:
    bounded = min(max(value, 1e-6), 1.0 - 1e-6)
    return math.log(bounded / (1.0 - bounded))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def aggregate_claim(
    atoms: tuple[EvidenceAtom, ...] | list[EvidenceAtom],
    parameters: ClaimParameters | None = None,
    *,
    now: datetime | None = None,
) -> ClaimAssessment:
    """Aggregate one claim with bounded, independence-aware confidence."""

    if not atoms:
        raise ValueError("at least one evidence atom is required")
    params = parameters or ClaimParameters()
    reference_time = now or datetime.now(UTC)
    support_groups: dict[str, float] = {}
    contradiction_groups: dict[str, float] = {}
    for atom in atoms:
        mass = min(
            params.max_group_mass,
            atom.strength * atom.reliability * _freshness_factor(atom, reference_time),
        )
        if atom.polarity is EvidencePolarity.UNKNOWN:
            continue
        target = (
            support_groups if atom.polarity is EvidencePolarity.SUPPORTS else contradiction_groups
        )
        target[atom.independence_key] = max(target.get(atom.independence_key, 0.0), mass)

    support_mass = sum(support_groups.values())
    contradiction_mass = sum(contradiction_groups.values())
    total_mass = support_mass + contradiction_mass
    coverage = 1.0 - math.exp(-params.coverage_rate * total_mass)
    belief_true = _sigmoid(
        _logit(params.prior)
        + params.support_weight * support_mass
        - params.contradiction_weight * contradiction_mass
    )
    conflict = (
        min(support_mass, contradiction_mass) / (max(support_mass, contradiction_mass) + 1e-9)
        if total_mass
        else 0.0
    )
    certainty = coverage * (1.0 - params.conflict_penalty * conflict)
    first = atoms[0]
    return ClaimAssessment(
        claim=first.claim,
        subject=first.subject,
        profile_id=first.profile_id,
        belief_true=round(belief_true, 8),
        certainty=round(min(max(certainty, 0.0), 1.0), 8),
        coverage=round(min(max(coverage, 0.0), 1.0), 8),
        conflict=round(min(max(conflict, 0.0), 1.0), 8),
        support_mass=round(support_mass, 8),
        contradiction_mass=round(contradiction_mass, 8),
        evidence_ids=tuple(atom.evidence_id for atom in atoms),
    )


def aggregate_bundle(
    atoms: tuple[EvidenceAtom, ...] | list[EvidenceAtom],
    parameters: dict[tuple[ClaimKind, SourceFamily], ClaimParameters] | None = None,
    *,
    policy_version: str = "evidence-v1",
    now: datetime | None = None,
) -> EvidenceBundle:
    """Group and aggregate atoms deterministically for replay."""

    grouped: dict[tuple[ClaimKind, str, str | None], list[EvidenceAtom]] = {}
    for atom in atoms:
        grouped.setdefault((atom.claim, atom.subject, atom.profile_id), []).append(atom)
    assessments: list[ClaimAssessment] = []
    for key in sorted(grouped, key=lambda value: (value[0].value, value[1], value[2] or "")):
        group = grouped[key]
        policy = (parameters or {}).get((key[0], group[0].source_family), ClaimParameters())
        assessments.append(aggregate_claim(group, policy, now=now))
    return EvidenceBundle(
        atoms=tuple(atoms), assessments=tuple(assessments), policy_version=policy_version
    )
