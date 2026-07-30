"""Bounded parallel evidence production and deterministic aggregation."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence  # noqa: TC003
from typing import Protocol

from job_ftch.domain import (
    AssessedJob,
    ClaimKind,
    ClaimParameters,
    EvidenceAtom,
    EvidenceBundle,
    EvidencePolarity,
    EvidenceProvenance,
    JobRecord,
    JobStatus,
    SourceFamily,
    SourceIdentity,
    aggregate_bundle,
    source_identity_for_parts,
)


class EvidenceProducer(Protocol):
    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]: ...


def _identity(item: JobRecord) -> SourceIdentity:
    return source_identity_for_parts(
        source_kind=item.source_kind,
        source_name=item.source_name,
        metadata=item.metadata,
    )


class MetadataEvidenceProducer:
    """Reads transitional serialized atoms while producers migrate one by one."""

    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]:
        atoms: list[EvidenceAtom] = []
        for raw in item.metadata.get("evidence_atoms", ()):
            if isinstance(raw, dict):
                try:
                    atoms.append(EvidenceAtom.model_validate(raw))
                except ValueError:
                    continue
        return tuple(atoms)


class ProfileScoreEvidenceProducer:
    """Converts existing per-profile features into auditable raw evidence.

    This is a migration producer. It intentionally does not use the new final
    score as a routing decision; each feature remains visible and calibratable.
    """

    _FEATURES = (
        "title_score",
        "skills_score",
        "domain_score",
        "seniority_score",
        "region_score",
        "salary_score",
        "culture_score",
        "vector_score",
    )

    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]:
        identity = _identity(item)
        atoms: list[EvidenceAtom] = []
        for score in item.profile_scores:
            for feature in self._FEATURES:
                value = float(getattr(score, feature))
                if value == 0.0 or 0.45 < value < 0.55:
                    continue
                polarity = (
                    EvidencePolarity.SUPPORTS if value >= 0.5 else EvidencePolarity.CONTRADICTS
                )
                atoms.append(
                    EvidenceAtom(
                        evidence_id=(f"{item.raw_item_id}:profile:{score.profile_id}:{feature}"),
                        claim=ClaimKind.PROFILE_RELEVANCE,
                        subject="profile_relevance",
                        profile_id=score.profile_id,
                        polarity=polarity,
                        strength=value if polarity is EvidencePolarity.SUPPORTS else 1.0 - value,
                        reliability=0.65,
                        provenance=EvidenceProvenance.INFERRED,
                        producer="profile_feature_scorer",
                        producer_version="profile-features-v1",
                        source_family=identity.family,
                        observation_kind=identity.observation_kind,
                        transport=identity.transport,
                        independence_key=(
                            f"{item.raw_item_id}:profile:{score.profile_id}:{feature}"
                        ),
                        observation_id=item.raw_item_id,
                        candidate_id=str(
                            item.metadata.get("candidate_span_id") or item.raw_item_id
                        ),
                        evidence_ref=f"profile_scores:{score.profile_id}:{feature}",
                    )
                )
            atoms.append(
                EvidenceAtom(
                    evidence_id=f"{item.raw_item_id}:constraint:{score.profile_id}",
                    claim=ClaimKind.HARD_CONSTRAINT,
                    subject="profile_constraints",
                    profile_id=score.profile_id,
                    polarity=(
                        EvidencePolarity.SUPPORTS
                        if score.hard_pass
                        else EvidencePolarity.CONTRADICTS
                    ),
                    strength=1.0,
                    reliability=0.9,
                    provenance=EvidenceProvenance.INFERRED,
                    producer="profile_constraint_checker",
                    producer_version="constraints-v1",
                    source_family=identity.family,
                    observation_kind=identity.observation_kind,
                    transport=identity.transport,
                    independence_key=f"{item.raw_item_id}:constraint:{score.profile_id}",
                    observation_id=item.raw_item_id,
                    candidate_id=str(item.metadata.get("candidate_span_id") or item.raw_item_id),
                    evidence_ref=f"profile_scores:{score.profile_id}:hard_pass",
                )
            )
        return tuple(atoms)


class ProfileLexicalEvidenceProducer:
    """Convert explicit profile anti-preference matches into typed evidence.

    The producer is deliberately one-sided: negative lexical matches can
    create a relevance conflict, but keyword presence can never auto-accept a
    vacancy.  The terminal owner resolves the conflict together with LLM
    evidence.
    """

    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]:
        matches = item.metadata.get("lexical_profile_matches", {})
        if not isinstance(matches, dict):
            return ()
        identity = _identity(item)
        atoms: list[EvidenceAtom] = []
        for profile_id, values in sorted(matches.items()):
            if not isinstance(profile_id, str) or not isinstance(values, dict):
                continue
            negative = values.get("negative", ())
            if not isinstance(negative, (list, tuple)):
                continue
            for phrase in sorted({str(value) for value in negative if str(value).strip()}):
                atoms.append(
                    EvidenceAtom(
                        evidence_id=f"{item.raw_item_id}:lexical-negative:{profile_id}:{phrase}",
                        claim=ClaimKind.PROFILE_RELEVANCE,
                        subject="profile_relevance",
                        profile_id=profile_id,
                        polarity=EvidencePolarity.CONTRADICTS,
                        strength=0.95,
                        reliability=0.9,
                        provenance=EvidenceProvenance.INFERRED,
                        producer="profile_lexical_evidence",
                        producer_version="profile-lexical-v1",
                        source_family=identity.family,
                        observation_kind=identity.observation_kind,
                        transport=identity.transport,
                        independence_key=(
                            f"{item.raw_item_id}:profile:{profile_id}:lexical-negative:{phrase}"
                        ),
                        observation_id=item.raw_item_id,
                        candidate_id=str(
                            item.metadata.get("candidate_span_id") or item.raw_item_id
                        ),
                        evidence_ref=f"profile:anti_preference:{phrase}",
                    )
                )
        return tuple(atoms)


class RiskEvidenceProducer:
    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]:
        identity = _identity(item)
        return tuple(
            EvidenceAtom(
                evidence_id=f"{item.raw_item_id}:risk:{index}",
                claim=ClaimKind.RISK,
                subject=str(signal),
                polarity=EvidencePolarity.SUPPORTS,
                strength=1.0,
                reliability=0.8,
                provenance=EvidenceProvenance.INFERRED,
                producer="risk_signal_rules",
                producer_version="risk-v1",
                source_family=identity.family,
                observation_kind=identity.observation_kind,
                transport=identity.transport,
                independence_key=f"{item.raw_item_id}:risk:{signal}",
                observation_id=item.raw_item_id,
                candidate_id=str(item.metadata.get("candidate_span_id") or item.raw_item_id),
                evidence_ref=f"job:risk_signals:{index}",
            )
            for index, signal in enumerate(item.risk_signals)
        )


class LifecycleQualityEvidenceProducer:
    """Turns cheap lifecycle and completeness fields into explicit evidence."""

    async def produce(self, item: JobRecord) -> tuple[EvidenceAtom, ...]:
        identity = _identity(item)
        atoms: list[EvidenceAtom] = []
        state = str(item.metadata.get("freshness_evidence_state", "missing"))
        status = item.status
        if status is JobStatus.FILLED or state in {"closed", "filled", "expired"}:
            polarity, strength, reliability = EvidencePolarity.CONTRADICTS, 1.0, 0.9
        elif status is JobStatus.OPEN or state in {"explicit_status", "observed_at"}:
            polarity, strength, reliability = EvidencePolarity.SUPPORTS, 0.8, 0.8
        else:
            polarity, strength, reliability = EvidencePolarity.SUPPORTS, 0.1, 0.2
        atoms.append(
            EvidenceAtom(
                evidence_id=f"{item.raw_item_id}:freshness",
                claim=ClaimKind.FRESHNESS,
                subject="active_vacancy",
                polarity=polarity,
                strength=strength,
                reliability=reliability,
                provenance=EvidenceProvenance.INFERRED,
                producer="lifecycle_quality_rules",
                producer_version="lifecycle-quality-v1",
                source_family=identity.family,
                observation_kind=identity.observation_kind,
                transport=identity.transport,
                independence_key=f"{item.raw_item_id}:freshness",
                observation_id=item.raw_item_id,
                candidate_id=str(item.metadata.get("candidate_span_id") or item.raw_item_id),
                evidence_ref="job:metadata:freshness_evidence_state",
            )
        )
        quality = item.quality_score
        if quality is not None:
            quality_polarity = (
                EvidencePolarity.SUPPORTS if quality >= 0.5 else EvidencePolarity.CONTRADICTS
            )
            atoms.append(
                EvidenceAtom(
                    evidence_id=f"{item.raw_item_id}:quality",
                    claim=ClaimKind.EVIDENCE_QUALITY,
                    subject="structured_quality",
                    polarity=quality_polarity,
                    strength=quality
                    if quality_polarity is EvidencePolarity.SUPPORTS
                    else 1.0 - quality,
                    reliability=0.75,
                    provenance=EvidenceProvenance.INFERRED,
                    producer="lifecycle_quality_rules",
                    producer_version="lifecycle-quality-v1",
                    source_family=identity.family,
                    observation_kind=identity.observation_kind,
                    transport=identity.transport,
                    independence_key=f"{item.raw_item_id}:quality",
                    observation_id=item.raw_item_id,
                    candidate_id=str(item.metadata.get("candidate_span_id") or item.raw_item_id),
                    evidence_ref="job:quality_score",
                )
            )
        return tuple(atoms)


class EvidenceFanOutNode:
    """One typed stage containing the only parallel policy fan-out."""

    def __init__(
        self,
        producers: Sequence[EvidenceProducer] | None = None,
        *,
        parameters: dict[tuple[ClaimKind, SourceFamily], ClaimParameters] | None = None,
        policy_version: str = "evidence-v1",
        timeout_seconds: float = 2.0,
    ) -> None:
        self._producers = tuple(
            producers
            or (
                MetadataEvidenceProducer(),
                ProfileScoreEvidenceProducer(),
                ProfileLexicalEvidenceProducer(),
                RiskEvidenceProducer(),
                LifecycleQualityEvidenceProducer(),
            )
        )
        self._parameters = parameters or {}
        self._policy_version = policy_version
        self._timeout_seconds = timeout_seconds

    async def process(self, item: JobRecord) -> AssessedJob:
        tasks = [asyncio.create_task(producer.produce(item)) for producer in self._producers]
        timed_out = False
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            results = []
        atoms: list[EvidenceAtom] = []
        degraded: list[str] = ["evidence_fanout_timeout"] if timed_out else []
        for producer, result in zip(self._producers, results, strict=False):
            if isinstance(result, BaseException):
                degraded.append(f"evidence_producer_failed:{type(producer).__name__}")
                continue
            atoms.extend(result)
        atoms.sort(key=lambda atom: atom.evidence_id)
        bundle: EvidenceBundle = aggregate_bundle(
            atoms,
            self._parameters,
            policy_version=self._policy_version,
        )
        return AssessedJob(
            record=item,
            evidence=bundle.atoms,
            assessments=bundle.assessments,
            policy_version=bundle.policy_version,
            degradation_reasons=tuple(degraded),
        )
