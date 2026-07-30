"""Typed payloads at the evidence and decision boundaries."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from job_ftch.domain.evidence import ClaimAssessment, EvidenceAtom  # noqa: TC001
from job_ftch.domain.models import JobRecord, MatchDecision  # noqa: TC001


class WorkState(StrEnum):
    READY = "ready"
    DEFERRED = "deferred"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class AssessedJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: JobRecord
    evidence: tuple[EvidenceAtom, ...] = ()
    assessments: tuple[ClaimAssessment, ...] = ()
    policy_version: str = Field(default="evidence-v1", min_length=1)
    degradation_reasons: tuple[str, ...] = ()


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessed_job: AssessedJob
    routing_decision: MatchDecision | None = None
    work_state: WorkState = WorkState.READY
    reasons: tuple[str, ...] = ()
