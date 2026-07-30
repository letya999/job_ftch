from __future__ import annotations

from job_ftch.application.release_gates import evaluate_release_gates


def test_release_gates_block_all_explicit_safety_regressions() -> None:
    violations = evaluate_release_gates(
        [
            {"observation_id": "a", "decision": "accept", "risk_level": "high"},
            {"observation_id": "b", "decision": "reject", "reranker_unavailable": True},
            {
                "observation_id": "c",
                "terminal_drop": True,
                "stage": "node",
                "reason": "x",
                "evidence": "y",
                "version": "v1",
                "lanes": ["main", "review"],
                "changed_content_skipped": True,
                "retryable_failure_duplicate": True,
                "candidate_loss": True,
                "synthetic_input_only_improvement": True,
                "source_coverage_regression": True,
            },
        ]
    )

    assert {violation.code for violation in violations} == {
        "accepted_high_risk",
        "reranker_silent_reject",
        "overlapping_output_lanes",
        "changed_content_skipped",
        "retryable_failure_duplicate",
        "one_to_many_candidate_loss",
        "synthetic_input_only_improvement",
        "unexplained_source_coverage_regression",
    }


def test_terminal_drop_gate_accepts_complete_record_and_explained_coverage() -> None:
    assert not evaluate_release_gates(
        [
            {
                "observation_id": "ok",
                "terminal_drop": True,
                "stage": "HardVeto",
                "reason": "high_risk",
                "evidence": "risk signal",
                "version": "v1",
                "source_coverage_regression": True,
                "source_health_explanation": "source paused",
            }
        ]
    )
