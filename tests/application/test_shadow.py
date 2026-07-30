from __future__ import annotations

import pytest

from job_ftch.application.release_gates import evaluate_release_gates
from job_ftch.application.shadow import (
    CanaryScope,
    ShadowArtifact,
    ShadowDecision,
    compare_shadow_decisions,
    run_shadow_canary,
)
from job_ftch.domain import MatchDecision


def test_shadow_comparison_is_keyed_by_common_raw_observation() -> None:
    baseline = [
        ShadowDecision("a", MatchDecision.REJECT, "telegram", "ai"),
        ShadowDecision("b", MatchDecision.ACCEPT, "career", "backend"),
    ]
    candidate = [
        ShadowDecision("a", MatchDecision.REVIEW, "telegram", "ai"),
        ShadowDecision("b", MatchDecision.ACCEPT, "career", "backend"),
        ShadowDecision("new", MatchDecision.ACCEPT, "career", "backend"),
    ]

    report = compare_shadow_decisions(baseline, candidate)

    assert report.compared == 2
    assert report.disagreements == 1
    assert report.by_source == {"telegram": 1}
    assert report.by_profile == {"ai": 1}


def test_canary_runs_only_a_single_source_profile_slice_and_emits_gate_records() -> None:
    baseline = ShadowArtifact(
        policy_version="new",
        decisions=(
            ShadowDecision("a", MatchDecision.REJECT, "telegram", "ai"),
            ShadowDecision("b", MatchDecision.ACCEPT, "career", "backend"),
        ),
    )
    candidate = ShadowArtifact(
        policy_version="new",
        decisions=(
            ShadowDecision(
                "a",
                MatchDecision.ACCEPT,
                "telegram",
                "ai",
                gate_signals=(("risk_level", "high"),),
            ),
            ShadowDecision("b", MatchDecision.ACCEPT, "career", "backend"),
        ),
    )

    report = run_shadow_canary(
        baseline,
        candidate,
        scope=CanaryScope(source="telegram", profile_id="ai"),
    )

    assert report.compared == 1
    assert report.changes[0].observation_id == "a"
    scoped_candidate = candidate.for_canary(CanaryScope(source="telegram", profile_id="ai"))
    assert {
        violation.code
        for violation in evaluate_release_gates(scoped_candidate.release_gate_records())
    } == {"accepted_high_risk"}


def test_shadow_canary_rejects_different_raw_observation_sets() -> None:
    baseline = ShadowArtifact("old", (ShadowDecision("old", MatchDecision.REJECT, "telegram"),))
    candidate = ShadowArtifact("new", (ShadowDecision("new", MatchDecision.REJECT, "telegram"),))

    with pytest.raises(ValueError, match="identical raw observation IDs"):
        run_shadow_canary(baseline, candidate)


def test_shadow_artifact_rejects_duplicate_raw_observation() -> None:
    duplicate = ShadowDecision("same", MatchDecision.REJECT, "telegram")

    with pytest.raises(ValueError, match="one decision per observation"):
        ShadowArtifact("new", (duplicate, duplicate))
