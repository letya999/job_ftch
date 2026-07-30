from __future__ import annotations

from job_ftch.application.graph.policy import DecisionPolicy


def test_policy_deduplicates_same_independence_group_and_records_missing() -> None:
    evaluation = DecisionPolicy(
        {
            "mode": "claims",
            "claims": {
                "profile_relevance": {
                    "aggregation": "weighted_mean",
                    "features": {
                        "dense_margin": {"transform": "clamp01", "weight": 0.5},
                        "llm_relevance": {"transform": "clamp01", "weight": 0.5},
                    },
                }
            },
        }
    ).evaluate({"_llm_relevance": {"confidence": 0.8}}, None)
    assert evaluation["score"] == 0.8
    assert evaluation["missing"] == ["dense_margin"]
    assert evaluation["signals"][0]["name"] == "llm_relevance"


def test_policy_veto_is_explained_in_trace() -> None:
    evaluation = DecisionPolicy(
        {
            "mode": "weighted",
            "signals": [{"name": "parallel_final_score", "weight": 1.0}],
            "accept_threshold": 0.5,
            "vetoes": [{"name": "quality_score", "lt": 0.6}],
        }
    ).evaluate({"parallel_final_score": 0.9, "quality_score": 0.2})
    assert evaluation["decision"] == "reject"
    assert evaluation["vetoes"][0]["name"] == "quality_score"
