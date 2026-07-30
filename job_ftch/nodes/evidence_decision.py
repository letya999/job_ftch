"""Compatibility bridge from the legacy JobRecord pipeline to the new policy."""

from __future__ import annotations

from job_ftch.application.evidence_policy import load_evidence_parameters
from job_ftch.config import Settings
from job_ftch.domain import JobRecord  # noqa: TC001
from job_ftch.nodes.decision import DecisionNode
from job_ftch.nodes.evidence_fanout import EvidenceFanOutNode
from job_ftch.nodes.need_more_evidence import NeedMoreEvidenceNode


class EvidenceDecisionNode:
    """Run fan-out and the single DecisionNode inside one legacy stage.

    The outer pipeline currently transports ``JobRecord`` values. This bridge
    lets the new typed assessment boundary land without introducing a second
    orchestrator. Deferred lifecycle handling is persisted in metadata until
    the durable resolver worker is wired in the next task.
    """

    def __init__(
        self,
        fanout: EvidenceFanOutNode | None = None,
        decision: DecisionNode | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings()
        self._fanout = fanout or EvidenceFanOutNode(
            parameters=load_evidence_parameters(settings.evidence_policy_path),
            policy_version=settings.pipeline_decision_version,
        )
        self._decision = decision or DecisionNode()
        self._need_more = NeedMoreEvidenceNode()

    async def process(self, item: JobRecord) -> JobRecord:
        assessed = await self._fanout.process(item)
        result = await self._decision.process(assessed)
        if result.work_state.value == "deferred":
            assessed = await self._need_more.process(result.assessed_job)
            result = result.model_copy(update={"assessed_job": assessed})
        metadata = dict(result.assessed_job.record.metadata)
        metadata["evidence_atoms"] = [
            atom.model_dump(mode="json") for atom in result.assessed_job.evidence
        ]
        metadata["evidence_assessments"] = [
            assessment.model_dump(mode="json") for assessment in result.assessed_job.assessments
        ]
        metadata["evidence_policy_version"] = result.assessed_job.policy_version
        metadata["decision_reasons"] = result.reasons
        if result.work_state.value == "deferred":
            metadata["deferred_reason"] = (
                result.reasons[-1] if result.reasons else "evidence_missing"
            )
        metadata["work_state"] = result.work_state.value
        return result.assessed_job.record.model_copy(
            update={
                "review_reasons": tuple(
                    dict.fromkeys((*result.assessed_job.record.review_reasons, *result.reasons))
                ),
                "metadata": metadata,
            }
        )
