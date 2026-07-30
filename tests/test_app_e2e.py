from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _offline_cli_env(**overrides: str) -> dict[str, str]:
    return {
        **os.environ,
        "JOB_FTCH_STORE_BACKEND": "memory",
        "JOB_FTCH_JOB_GROUP_STORE_BACKEND": "memory",
        "JOB_FTCH_EMBEDDING_ENABLED": "false",
        "JOB_FTCH_EMBEDDING_PREFILTER_ENABLED": "false",
        "JOB_FTCH_BGEM3_ENABLED": "false",
        "JOB_FTCH_RELEVANCE_BACKEND": "keywords",
        "JOB_FTCH_RELEVANCE_SHOT_BACKEND": "memory",
        "JOB_FTCH_LLM_BACKEND": "heuristic",
        "JOB_FTCH_LLM_RELEVANCE_MAX_PER_RUN": "0",
        "JOB_FTCH_LLM_PRESENTABLE_ENABLED": "false",
        **overrides,
    }


def test_app_processes_multisource_fixture_end_to_end(tmp_path: Path) -> None:
    output_path = tmp_path / "multisource-output.json"
    quarantine_path = tmp_path / "multisource-quarantine.jsonl"
    rejected_path = tmp_path / "multisource-rejected.jsonl"
    review_path = tmp_path / "multisource-review.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "job_ftch",
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
        env=_offline_cli_env(
            JOB_FTCH_QUARANTINE_OUTPUT_PATH=str(quarantine_path),
            JOB_FTCH_REJECTED_OUTPUT_PATH=str(rejected_path),
            JOB_FTCH_REVIEW_OUTPUT_PATH=str(review_path),
            JOB_FTCH_CAREER_SITE_ALLOWED_HOSTS='["job-boards.greenhouse.io","www.bcc.kz","bcc.kz"]',
        ),
    )

    assert result.returncode == 0, result.stderr
    emitted_payload = json.loads(output_path.read_text(encoding="utf-8"))
    emitted = emitted_payload["items"]
    rejected_records = [
        json.loads(line)
        for line in rejected_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert emitted_payload["schema_version"] == "job_ftch.job.v1"
    # Precision-first runtime never auto-accepts without a configured profile.
    assert emitted == []
    assert Counter(record["payload"]["reason"] for record in rejected_records) == {
        "low_relevance_prefilter": 3
    }
    assert len(review_path.read_text(encoding="utf-8").splitlines()) == 4
    assert quarantine_path.read_text(encoding="utf-8") == ""


def test_app_quarantines_multisource_negative_fixture_end_to_end(tmp_path: Path) -> None:
    output_path = tmp_path / "negative-output.json"
    quarantine_path = tmp_path / "negative-quarantine.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "job_ftch",
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
        env=_offline_cli_env(JOB_FTCH_QUARANTINE_OUTPUT_PATH=str(quarantine_path)),
    )

    assert result.returncode == 0, result.stderr
    quarantine_lines = quarantine_path.read_text(encoding="utf-8").splitlines()
    quarantine_records = [json.loads(line) for line in quarantine_lines]

    output_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert output_payload["schema_version"] == "job_ftch.job.v1"
    assert output_payload["items"] == []
    assert len(quarantine_records) == 5
    assert Counter(record["payload"]["reason"] for record in quarantine_records) == {
        "disallowed_url_host": 1,
        "invalid_raw_item": 2,
        "invalid_origin_url": 1,
        "empty_source_name": 1,
    }
    assert any(
        record["payload"]["snapshot"].get("line_number") == 6 for record in quarantine_records
    )
