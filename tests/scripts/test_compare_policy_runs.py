from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module() -> object:
    path = Path("scripts/eval/compare_policy_runs.py")
    spec = importlib.util.spec_from_file_location("compare_policy_runs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_reports_integer_labels_and_item_transitions(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(
        json.dumps(
            [
                {"stable_id": "a", "relevant": 1, "decision": "accept"},
                {"stable_id": "b", "relevant": 0, "decision": "reject"},
            ]
        ),
        encoding="utf-8",
    )
    candidate.write_text(
        json.dumps(
            [
                {"stable_id": "a", "relevant": 1, "decision": "reject"},
                {"stable_id": "b", "relevant": 0, "decision": "reject"},
            ]
        ),
        encoding="utf-8",
    )
    result = _module().compare(baseline, candidate)  # type: ignore[attr-defined]
    assert result["deltas"]["recall"] == -1.0
    assert result["transitions"]["TP->FN"] == ["a"]
    assert result["regression_items"] == ["a"]
    assert result["promotion"]["status"] == "inconclusive"
