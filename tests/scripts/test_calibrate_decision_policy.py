from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module() -> object:
    path = Path("scripts/eval/calibrate_decision_policy.py")
    spec = importlib.util.spec_from_file_location("calibrate_decision_policy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calibration_excludes_derived_final_score_and_emits_reproducible_provenance(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "features.jsonl"
    rows = [
        {
            "relevant": 1,
            "split": "train",
            "features": {"llm": 1.0, "title_score": 0.8, "final_score": 0.9},
        },
        {
            "relevant": 1,
            "split": "train",
            "features": {"llm": 0.9, "title_score": 0.7, "final_score": 0.8},
        },
        {
            "relevant": 0,
            "split": "train",
            "features": {"llm": 0.1, "title_score": 0.2, "final_score": 0.1},
        },
        {
            "relevant": 0,
            "split": "holdout",
            "features": {"llm": 0.2, "title_score": 0.1, "final_score": 0.2},
        },
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    artifact = _module().calibrate(
        dataset, target="relevant", precision_floor=0.8, training_split="train"
    )

    assert artifact["version"] == "calibrated-policy/v1"
    assert "final_score" not in artifact["coefficients"]
    assert artifact["training_metrics"]["precision"] >= 0.8
    assert len(artifact["provenance"]["dataset_sha256"]) == 64
    assert artifact["training_split"] == "train"
    assert "llm" in artifact["point_biserial_target"]
