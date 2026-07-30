"""Tests for building a prefilter dataset from manual labels."""

import json
import subprocess
from pathlib import Path

SCRIPT = Path("scripts/eval/build_prefilter_dataset_from_manual_labels.py")
PYTHON = str(Path(".venv/Scripts/python.exe"))


def test_build_prefilter_dataset_from_manual_labels(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "\n".join(
            [
                json.dumps({"idx": 1, "stable_id": "a", "text": "Data Engineer", "url": "u1"}),
                json.dumps({"idx": 2, "stable_id": "b", "text": "Product Manager", "url": "u2"}),
            ]
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            [
                {"idx": 1, "gold": True, "reason": "de"},
                {"idx": 2, "gold": False, "reason": "pm"},
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "dataset.jsonl"

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
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"stable_id": "a", "text": "Data Engineer", "relevant": 1, "source_id": None, "url": "u1"},
        {
            "stable_id": "b",
            "text": "Product Manager",
            "relevant": 0,
            "source_id": None,
            "url": "u2",
        },
    ]


def test_build_prefilter_dataset_rejects_missing_labels(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        json.dumps({"idx": 1, "stable_id": "a", "text": "Data Engineer"}) + "\n",
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text("[]", encoding="utf-8")
    out = tmp_path / "dataset.jsonl"

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

    assert result.returncode != 0
    assert "label coverage mismatch" in result.stderr
    assert not out.exists()
