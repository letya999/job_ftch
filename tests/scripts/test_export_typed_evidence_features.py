from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path("scripts/eval/export_typed_evidence_features.py")
    spec = importlib.util.spec_from_file_location("export_typed_evidence_features", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_collapses_same_independence_group_and_retains_parent_group() -> None:
    module = _module()
    payload = {
        "results": [
            {
                "stable_id": "candidate-a",
                "parent_stable_id": "parent-a",
                "source_kind": "telegram_channel",
                "source_name": "jobs",
                "gold_relevant": 1,
                "decision_trace": {
                    "typed_evidence": {
                        "policy_version": "evidence-v2",
                        "degradation_reasons": ["timeout"],
                        "atoms": [
                            {
                                "claim": "profile_relevance",
                                "polarity": "supports",
                                "independence_key": "same",
                                "strength": 0.4,
                                "reliability": 0.8,
                            },
                            {
                                "claim": "profile_relevance",
                                "polarity": "supports",
                                "independence_key": "same",
                                "strength": 0.9,
                                "reliability": 0.8,
                            },
                        ],
                        "assessments": [
                            {
                                "claim": "profile_relevance",
                                "belief_true": 0.9,
                                "certainty": 0.7,
                                "coverage": 0.6,
                                "conflict": 0.1,
                                "support_mass": 0.5,
                                "contradiction_mass": 0.0,
                            }
                        ],
                    }
                },
            }
        ]
    }

    rows, skipped = module.export(payload, target="gold_relevant")

    assert skipped == 0
    assert rows[0]["group"] == "parent-a"
    assert rows[0]["features"]["atom.profile_relevance.supports.count"] == 1.0
    assert rows[0]["features"]["atom.profile_relevance.supports.strength_max"] == 0.9
    assert rows[0]["features"]["assessment.profile_relevance.belief_true_max"] == 0.9
    assert rows[0]["features"]["evidence.degradation_count"] == 1.0


def test_export_refuses_replay_without_typed_evidence() -> None:
    module = _module()

    with pytest.raises(ValueError, match="no binary-labelled"):
        module.export({"results": [{"stable_id": "x", "gold_relevant": 1}]}, target="gold_relevant")
