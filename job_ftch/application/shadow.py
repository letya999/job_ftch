"""Non-delivering shadow and canary artifacts for raw replay runs.

The helpers in this module deliberately work on terminal decision artifacts,
not on pipeline objects.  This makes a canary reproducible from persisted raw
observation IDs and guarantees that it cannot send output to a sink.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from job_ftch.domain import MatchDecision


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """One terminal policy result keyed by its immutable raw observation."""

    observation_id: str
    decision: MatchDecision
    source: str
    profile_id: str | None = None
    gate_signals: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not self.observation_id.strip():
            raise ValueError("observation_id must not be blank")
        if not self.source.strip():
            raise ValueError("source must not be blank")
        if len({key for key, _ in self.gate_signals}) != len(self.gate_signals):
            raise ValueError("gate signal keys must be unique")

    def release_gate_record(self) -> dict[str, Any]:
        """Return a lossless gate input record for the candidate decision."""
        return {
            "observation_id": self.observation_id,
            "decision": self.decision.value,
            "source": self.source,
            "profile_id": self.profile_id,
            **dict(self.gate_signals),
        }


@dataclass(frozen=True, slots=True)
class ShadowArtifact:
    """Versioned decision artifact retained for a shadow or canary run."""

    policy_version: str
    decisions: tuple[ShadowDecision, ...]

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")
        observation_ids = [decision.observation_id for decision in self.decisions]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("a shadow artifact must contain one decision per observation")

    def decision_ids(self) -> frozenset[str]:
        return frozenset(decision.observation_id for decision in self.decisions)

    def for_canary(self, scope: CanaryScope) -> ShadowArtifact:
        return ShadowArtifact(
            policy_version=self.policy_version,
            decisions=tuple(decision for decision in self.decisions if scope.matches(decision)),
        )

    def release_gate_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(decision.release_gate_record() for decision in self.decisions)


@dataclass(frozen=True, slots=True)
class CanaryScope:
    """The single source family and profile allowed to receive a canary."""

    source: str
    profile_id: str

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.profile_id.strip():
            raise ValueError("a canary requires one non-blank source and profile_id")

    def matches(self, decision: ShadowDecision) -> bool:
        return decision.source == self.source and decision.profile_id == self.profile_id


@dataclass(frozen=True, slots=True)
class ShadowChange:
    observation_id: str
    baseline: MatchDecision
    candidate: MatchDecision
    source: str
    profile_id: str | None


@dataclass(frozen=True, slots=True)
class ShadowReport:
    compared: int
    disagreements: int
    by_source: dict[str, int]
    by_profile: dict[str, int]
    changes: tuple[ShadowChange, ...] = ()


def compare_shadow_decisions(
    baseline: Iterable[ShadowDecision], candidate: Iterable[ShadowDecision]
) -> ShadowReport:
    """Compare terminal decisions only; this function has no delivery side effects."""
    baseline_by_id = {decision.observation_id: decision for decision in baseline}
    candidate_by_id = {decision.observation_id: decision for decision in candidate}
    common_ids = baseline_by_id.keys() & candidate_by_id.keys()
    by_source: Counter[str] = Counter()
    by_profile: Counter[str] = Counter()
    changes: list[ShadowChange] = []
    disagreements = 0
    for observation_id in sorted(common_ids):
        old = baseline_by_id[observation_id]
        new = candidate_by_id[observation_id]
        if old.decision == new.decision:
            continue
        disagreements += 1
        by_source[old.source] += 1
        by_profile[old.profile_id or "unprofiled"] += 1
        changes.append(
            ShadowChange(
                observation_id=observation_id,
                baseline=old.decision,
                candidate=new.decision,
                source=old.source,
                profile_id=old.profile_id,
            )
        )
    return ShadowReport(
        compared=len(common_ids),
        disagreements=disagreements,
        by_source=dict(by_source),
        by_profile=dict(by_profile),
        changes=tuple(changes),
    )


def run_shadow_canary(
    baseline: ShadowArtifact,
    candidate: ShadowArtifact,
    *,
    scope: CanaryScope | None = None,
) -> ShadowReport:
    """Validate same-input replay and compare it, optionally in a canary slice.

    A shadow result is meaningful only if both graphs consumed precisely the
    same raw observation IDs.  Rejecting unmatched artifacts here prevents a
    deployment from interpreting a source outage or a synthetic input change
    as a policy improvement.
    """
    if scope is not None:
        baseline = baseline.for_canary(scope)
        candidate = candidate.for_canary(scope)
    if baseline.decision_ids() != candidate.decision_ids():
        missing_from_candidate = sorted(baseline.decision_ids() - candidate.decision_ids())
        missing_from_baseline = sorted(candidate.decision_ids() - baseline.decision_ids())
        raise ValueError(
            "shadow artifacts must contain identical raw observation IDs; "
            f"missing_from_candidate={missing_from_candidate}, "
            f"missing_from_baseline={missing_from_baseline}"
        )
    return compare_shadow_decisions(baseline.decisions, candidate.decisions)
