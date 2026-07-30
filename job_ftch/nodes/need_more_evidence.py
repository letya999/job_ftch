"""Select one concrete deferred claim without making a terminal decision."""

from __future__ import annotations

from job_ftch.domain import AssessedJob, ClaimAssessment, ClaimKind


class NeedMoreEvidenceNode:
    """Return the highest-priority unresolved claim for a resolver task."""

    _PRIORITY = (
        ClaimKind.IS_JOB,
        ClaimKind.PROFILE_RELEVANCE,
        ClaimKind.HARD_CONSTRAINT,
        ClaimKind.FRESHNESS,
        ClaimKind.RISK,
    )

    @classmethod
    def select(cls, item: AssessedJob) -> ClaimAssessment | None:
        for claim in cls._PRIORITY:
            candidates = [
                assessment for assessment in item.assessments if assessment.claim is claim
            ]
            if not candidates:
                continue
            uncertain = [assessment for assessment in candidates if assessment.certainty < 0.65]
            if uncertain:
                return min(uncertain, key=lambda assessment: assessment.certainty)
        return None

    async def process(self, item: AssessedJob) -> AssessedJob:
        # The selected claim is diagnostic metadata on the immutable record;
        # this node never changes routing or creates a terminal outcome.
        selected = self.select(item)
        if selected is None:
            return item
        record = item.record.model_copy(
            update={
                "metadata": {
                    **item.record.metadata,
                    "missing_critical_claim": f"{selected.claim.value}:{selected.subject}",
                }
            }
        )
        return item.model_copy(update={"record": record})
