from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from scripts import verify_observability_run


def test_verify_observability_run_fails_when_required_backends_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verify_observability_run,
        "_openobserve",
        lambda run_id: {"configured": False},
    )
    monkeypatch.setattr(
        verify_observability_run,
        "_langfuse",
        lambda run_id: {"configured": False},
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-openobserve",
            "--require-langfuse",
        ],
    )

    assert verify_observability_run.main() == 1


def test_verify_observability_run_writes_gate_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "telemetry.json"
    monkeypatch.setattr(
        verify_observability_run,
        "_openobserve",
        lambda run_id: {
            "configured": True,
            "search_status": 200,
            "metric_stream_status": 200,
            "hits": [{"rows": 3}],
        },
    )
    monkeypatch.setattr(
        verify_observability_run,
        "_langfuse",
        lambda run_id: {
            "configured": True,
            "trace_status": 200,
            "trace_found": True,
            "eval_item_trace_status": 200,
            "eval_item_traces": 2,
            "decision_spans": 4,
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-openobserve",
            "--require-langfuse",
            "--min-openobserve-rows",
            "3",
            "--expect-eval-item-traces",
            "2",
            "--expect-decision-spans",
            "4",
            "--out-json",
            str(output),
        ],
    )

    assert verify_observability_run.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["failures"] == []
    assert json.loads(capsys.readouterr().out)["run_id"] == "run-123"


def test_verify_observability_run_writes_fail_evidence_on_backend_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "telemetry.json"
    monkeypatch.setattr(
        verify_observability_run,
        "_openobserve",
        lambda run_id: {"configured": False},
    )

    def broken_langfuse(run_id: str) -> dict[str, object]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(verify_observability_run, "_langfuse", broken_langfuse)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-langfuse",
            "--out-json",
            str(output),
        ],
    )

    assert verify_observability_run.main() == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert "Langfuse probe failed" in payload["failures"][0]
