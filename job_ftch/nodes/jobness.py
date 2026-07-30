"""Independent jobness decision artifact (ADR-056)."""

from __future__ import annotations

from hashlib import sha256

from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    JobnessDecision,
    JobRecord,
    PostType,
    RawItem,
    StructuredSourceEvidence,
    source_identity_for_parts,
    source_identity_for_raw_item,
)


class RawJobnessEvidenceNode:
    """Produce cheap pre-extraction jobness evidence for a ``RawItem``."""

    async def process(self, item: RawItem) -> RawItem:
        metadata = dict(item.metadata)
        distribution = self._distribution(metadata)
        job_probability = distribution.get(PostType.JOB_POSTING, 0.0)
        uncertainty = 1.0 - max(distribution.values(), default=0.0)
        evidence = tuple(
            StructuredSourceEvidence.model_validate(raw)
            for raw in metadata.get("structured_source_evidence", [])
            if isinstance(raw, dict)
        )
        decision = JobnessDecision(
            job_probability=job_probability,
            # Parser completeness is deliberately not a hiring-intent source.
            hiring_intent=None,
            post_type_distribution=distribution,
            evidence=evidence,
            uncertainty=uncertainty,
        )
        # Keep the structured artifact only as a diagnostic during migration;
        # terminal policy consumes the typed IS_JOB atom below.
        metadata["jobness_diagnostic"] = decision.model_dump(mode="json")
        confidence = job_probability
        dominant_type, dominant_confidence = max(
            distribution.items(), key=lambda entry: entry[1], default=(PostType.UNKNOWN, 0.0)
        )
        is_non_job = dominant_type in {
            PostType.CANDIDATE_SEEKING,
            PostType.ANNOUNCEMENT,
            PostType.SPAM,
        }
        if confidence > 0 or is_non_job:
            identity = source_identity_for_raw_item(item)
            digest = sha256(f"jobness|{post_type_key(distribution)}".encode()).hexdigest()[:16]
            metadata.setdefault("evidence_atoms", []).append(
                EvidenceAtom(
                    evidence_id=f"{item.stable_id}:jobness:{digest}",
                    claim=ClaimKind.IS_JOB,
                    subject="vacancy",
                    polarity=(
                        EvidencePolarity.SUPPORTS
                        if confidence >= 0.5 and not is_non_job
                        else EvidencePolarity.CONTRADICTS
                    ),
                    strength=(
                        confidence if confidence >= 0.5 and not is_non_job else dominant_confidence
                    ),
                    reliability=0.85,
                    provenance=EvidenceProvenance.CLASSIFIER,
                    producer="jobness_classifier",
                    producer_version="jobness-v1",
                    source_family=identity.family,
                    observation_kind=identity.observation_kind,
                    transport=identity.transport,
                    independence_key=f"{item.stable_id}:post_type",
                    observation_id=item.stable_id,
                    candidate_id=str(metadata.get("candidate_span_id") or item.stable_id),
                    evidence_ref="metadata:post_type_distribution",
                ).model_dump(mode="json")
            )
        return item.model_copy(update={"metadata": metadata})

    @staticmethod
    def _distribution(metadata: dict[str, object]) -> dict[PostType, float]:
        raw = metadata.get("post_type_distribution")
        if isinstance(raw, dict):
            distribution: dict[PostType, float] = {}
            for key, value in raw.items():
                try:
                    post_type = PostType(str(key))
                    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                        continue
                    score = float(value)
                except ValueError:
                    continue
                if 0.0 <= score <= 1.0:
                    distribution[post_type] = score
            if distribution:
                return distribution
        label = metadata.get("preclassified_post_type", PostType.UNKNOWN.value)
        raw_confidence = metadata.get("preclassified_confidence", 0.0)
        confidence = (
            float(raw_confidence)
            if isinstance(raw_confidence, (int, float, str))
            and not isinstance(raw_confidence, bool)
            else 0.0
        )
        try:
            post_type = PostType(str(label))
        except ValueError:
            post_type = PostType.UNKNOWN
        confidence = min(1.0, max(0.0, confidence))
        return {post_type: confidence, PostType.UNKNOWN: 1.0 - confidence}


def post_type_key(distribution: dict[PostType, float]) -> str:
    """Create a stable digest input for deterministic evidence IDs."""
    return "|".join(f"{key.value}:{distribution[key]:.6f}" for key in sorted(distribution))


class JobnessEvidenceProducer:
    """Confirm jobness after extraction without changing the payload type.

    This distinct stage prevents the historical ``RawItem`` node from being
    silently inserted into a ``JobRecord`` spine.  It consumes the preserved
    post-type distribution when present and otherwise emits no certainty.
    """

    async def process(self, item: JobRecord) -> JobRecord:
        metadata = dict(item.metadata)
        distribution = RawJobnessEvidenceNode._distribution(metadata)
        job_probability = distribution.get(PostType.JOB_POSTING, 0.0)
        dominant_type, dominant_confidence = max(
            distribution.items(), key=lambda entry: entry[1], default=(PostType.UNKNOWN, 0.0)
        )
        is_non_job = dominant_type in {
            PostType.CANDIDATE_SEEKING,
            PostType.ANNOUNCEMENT,
            PostType.SPAM,
        }
        if job_probability <= 0 and not is_non_job:
            return item
        identity = source_identity_for_parts(
            source_kind=item.source_kind,
            source_name=item.source_name,
            metadata=item.metadata,
        )
        digest = sha256(
            f"post-extraction-jobness|{post_type_key(distribution)}".encode()
        ).hexdigest()[:16]
        metadata.setdefault("evidence_atoms", []).append(
            EvidenceAtom(
                evidence_id=f"{item.raw_item_id}:jobness:post:{digest}",
                claim=ClaimKind.IS_JOB,
                subject="vacancy",
                polarity=(
                    EvidencePolarity.SUPPORTS
                    if job_probability >= 0.5 and not is_non_job
                    else EvidencePolarity.CONTRADICTS
                ),
                strength=(
                    job_probability
                    if job_probability >= 0.5 and not is_non_job
                    else dominant_confidence
                ),
                reliability=0.85,
                provenance=EvidenceProvenance.CLASSIFIER,
                producer="jobness_post_extraction",
                producer_version="jobness-v2",
                source_family=identity.family,
                observation_kind=identity.observation_kind,
                transport=identity.transport,
                independence_key=f"{item.raw_item_id}:post_type",
                observation_id=item.raw_item_id,
                candidate_id=str(metadata.get("candidate_span_id") or item.raw_item_id),
                evidence_ref="metadata:post_type_distribution",
            ).model_dump(mode="json")
        )
        return item.model_copy(update={"metadata": metadata})


# Schema-v1 compatibility name.  New runtime graphs must use the explicit
# RawJobnessEvidenceNode or JobnessEvidenceProducer contract.
class JobnessDecisionNode(RawJobnessEvidenceNode):
    pass
