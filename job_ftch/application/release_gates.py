"""Machine-checkable Phase-8 release acceptance gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Any


@dataclass(frozen=True, slots=True)
class ReleaseGateViolation:
    observation_id: str
    code: str


def evaluate_release_gates(
    records: Iterable[Mapping[str, Any]],
) -> tuple[ReleaseGateViolation, ...]:
    """Return every explicit release blocker found in replay/canary artifacts."""
    violations: list[ReleaseGateViolation] = []
    for record in records:
        observation_id = str(record.get("observation_id") or "unknown")
        decision = record.get("decision")
        lanes = tuple(record.get("lanes") or ())
        if decision == "accept" and record.get("hard_constraint_violation"):
            violations.append(ReleaseGateViolation(observation_id, "accepted_hard_constraint"))
        if decision == "accept" and record.get("risk_level") == "high":
            violations.append(ReleaseGateViolation(observation_id, "accepted_high_risk"))
        if len(set(lanes)) > 1:
            violations.append(ReleaseGateViolation(observation_id, "overlapping_output_lanes"))
        if record.get("terminal_drop") and not all(
            record.get(field)
            for field in ("stage", "reason", "evidence", "version", "observation_id")
        ):
            violations.append(ReleaseGateViolation(observation_id, "incomplete_terminal_drop"))
        if record.get("changed_content_skipped"):
            violations.append(ReleaseGateViolation(observation_id, "changed_content_skipped"))
        if record.get("retryable_failure_duplicate"):
            violations.append(ReleaseGateViolation(observation_id, "retryable_failure_duplicate"))
        if record.get("candidate_loss"):
            violations.append(ReleaseGateViolation(observation_id, "one_to_many_candidate_loss"))
        if record.get("synthetic_input_only_improvement"):
            violations.append(
                ReleaseGateViolation(observation_id, "synthetic_input_only_improvement")
            )
        if record.get("reranker_unavailable") and decision == "reject":
            violations.append(ReleaseGateViolation(observation_id, "reranker_silent_reject"))
        if record.get("source_coverage_regression") and not record.get("source_health_explanation"):
            violations.append(
                ReleaseGateViolation(observation_id, "unexplained_source_coverage_regression")
            )
    return tuple(violations)
