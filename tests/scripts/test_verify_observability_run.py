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
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-openobserve",
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
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-openobserve",
            "--min-openobserve-rows",
            "3",
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

    def broken_openobserve(run_id: str) -> dict[str, object]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(verify_observability_run, "_openobserve", broken_openobserve)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_observability_run.py",
            "run-123",
            "--require-openobserve",
            "--out-json",
            str(output),
        ],
    )

    assert verify_observability_run.main() == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert "OpenObserve probe failed" in payload["failures"][0]
