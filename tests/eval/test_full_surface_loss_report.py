from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path("scripts/eval/full_surface_loss_report.py")
PYTHON = str(Path(".venv/Scripts/python.exe"))


def test_full_surface_loss_report_groups_false_negatives(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "eval_id": "tp",
                        "system_prediction": "positive",
                        "system_bucket": "persisted_job_url_match",
                        "stable_ids": ["s1"],
                        "url": "u1",
                        "routing_decision": "accept",
                    }
                ),
                json.dumps(
                    {
                        "eval_id": "fn_prefilter",
                        "system_prediction": "negative",
                        "system_bucket": "not_persisted",
                        "stable_ids": ["s2"],
                        "url": "u2",
                        "trace": {
                            "node_events": {
                                "hard_filter": {
                                    "node_id": "hard_filter",
                                    "outcome": "drop",
                                    "reason": "gate_returned_none",
                                }
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "eval_id": "tn",
                        "system_prediction": "negative",
                        "system_bucket": "not_persisted",
                        "stable_ids": ["s3"],
                        "url": "u3",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"positive_eval_ids": ["tp", "fn_prefilter"]}), encoding="utf-8")
    out = tmp_path / "loss.json"

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--candidates",
            str(candidates),
            "--labels",
            str(labels),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["metrics"]["tp"] == 1
    assert report["metrics"]["fn"] == 1
    assert report["false_negative_loss_counts"] == {"hard_filter": 1}
    assert report["lineage_flag_counts"] == {}


def test_full_surface_loss_report_does_not_treat_score_as_factual_drop(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "eval_id": "fn_prefilter",
                        "text": "noise noise",
                        "system_prediction": "negative",
                        "system_bucket": "not_persisted",
                        "stable_ids": ["s2"],
                        "url": "u2",
                        "relevance_prefilter_score": 0.01,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"positive_eval_ids": ["fn_prefilter"]}), encoding="utf-8")
    out = tmp_path / "loss.json"

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--candidates",
            str(candidates),
            "--labels",
            str(labels),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["false_negative_loss_counts"] == {"upstream_or_untraced_drop": 1}
