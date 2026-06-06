from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_app_processes_multisource_fixture_end_to_end(tmp_path: Path) -> None:
    output_path = tmp_path / "multisource-output.json"
    quarantine_path = tmp_path / "multisource-quarantine.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "app.py",
            "--source-path",
            "fixtures/e2e/multisource_positive.jsonl",
            "--output-path",
            str(output_path),
            "--max-items",
            "20",
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "JOB_FTCH_QUARANTINE_OUTPUT_PATH": str(quarantine_path),
        },
    )

    assert result.returncode == 0, result.stderr
    emitted = json.loads(output_path.read_text(encoding="utf-8"))

    assert len(emitted) == 8
    assert Counter(item["source_kind"] for item in emitted) == {
        "telegram_channel": 2,
        "telegram_group": 2,
        "telegram_comment": 2,
        "career_site": 2,
    }
    assert all(item["text"] for item in emitted)
    assert quarantine_path.read_text(encoding="utf-8") == ""


def test_app_quarantines_multisource_negative_fixture_end_to_end(tmp_path: Path) -> None:
    output_path = tmp_path / "negative-output.json"
    quarantine_path = tmp_path / "negative-quarantine.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "app.py",
            "--source-path",
            "fixtures/e2e/multisource_negative.jsonl",
            "--output-path",
            str(output_path),
            "--max-items",
            "20",
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "JOB_FTCH_QUARANTINE_OUTPUT_PATH": str(quarantine_path),
        },
    )

    assert result.returncode == 0, result.stderr
    quarantine_lines = quarantine_path.read_text(encoding="utf-8").splitlines()
    quarantine_records = [json.loads(line) for line in quarantine_lines]

    assert not output_path.exists()
    assert len(quarantine_records) == 6
    assert Counter(record["reason"] for record in quarantine_records) == {
        "disallowed_url_host": 2,
        "invalid_raw_item": 2,
        "invalid_origin_url": 1,
        "empty_source_name": 1,
    }
    assert any(record["snapshot"].get("line_number") == 6 for record in quarantine_records)
