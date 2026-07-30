"""Smoke tests for the classification eval harness (TD-002 / ADR-032)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_fixture(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def tiny_classification_fixture(tmp_path: Path) -> Path:
    """Build a 5-sample fixture with mixed post_types."""
    path = tmp_path / "labels.jsonl"
    records = [
        {
            "raw_item": {
                "source_kind": "telegram_channel",
                "source_name": "test",
                "external_id": "1",
                "url": "https://t.me/x/1",
                "text": "Ищем Senior Python Developer удаленно",
            },
            "labels": {"post_type": "job_posting", "routing_decision": "ACCEPT"},
        },
        {
            "raw_item": {
                "source_kind": "telegram_channel",
                "source_name": "test",
                "external_id": "2",
                "url": "https://t.me/x/2",
                "text": "Дайджест вакансий за неделю",
            },
            "labels": {"post_type": "announcement", "routing_decision": "DROP"},
        },
        {
            "raw_item": {
                "source_kind": "telegram_channel",
                "source_name": "test",
                "external_id": "3",
                "url": "https://t.me/x/3",
                "text": "Вебинар по ML, регистрация открыта",
            },
            "labels": {"post_type": "announcement", "routing_decision": "DROP"},
        },
        {
            "raw_item": {
                "source_kind": "telegram_channel",
                "source_name": "test",
                "external_id": "4",
                "url": "https://t.me/x/4",
                "text": "Open position: Frontend Engineer, React, remote",
            },
            "labels": {"post_type": "job_posting", "routing_decision": "ACCEPT"},
        },
        {
            "raw_item": {
                "source_kind": "telegram_channel",
                "source_name": "test",
                "external_id": "5",
                "url": "https://t.me/x/5",
                "text": "casino bonus code 2024",
            },
            "labels": {"post_type": "spam", "routing_decision": "DROP"},
        },
    ]
    _write_fixture(path, records)
    return path


@pytest.mark.anyio
async def test_classification_eval_writes_report(
    tiny_classification_fixture: Path, tmp_path: Path
) -> None:
    from scripts.evaluate_classification import evaluate

    output = tmp_path / "report.json"
    report = await evaluate(tiny_classification_fixture, limit=None)
    # Persist for the assertion below.
    with output.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["samples"] == 5
    assert "metrics_per_class" in payload
    assert "job_posting" in payload["metrics_per_class"]
    assert "announcement" in payload["metrics_per_class"]
    assert payload["false_positive_rate"] >= 0.0
    assert payload["valid_url_rate"] >= 0.0
    assert "llm_calls_per_100_items" in payload
    assert "gate_passed" in payload


def test_classification_eval_cli_smoke(tiny_classification_fixture: Path, tmp_path: Path) -> None:
    """End-to-end CLI smoke: subprocess invocation, --gate exit code."""
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_classification.py",
            "--fixture",
            str(tiny_classification_fixture),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
        env={"PYTHONIOENCODING": "utf-8", **__import__("os").environ},
    )
    assert result.returncode == 0, result.stderr
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["samples"] == 5
