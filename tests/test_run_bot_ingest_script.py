from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.run_bot_ingest import (
    _build_parser,
    _clear_output_artifacts,
    _resolve_user_id,
    _select_runtime,
    _write_report,
    main,
)


@pytest.mark.asyncio
async def test_resolve_user_id_prefers_explicit_value() -> None:
    runner = MagicMock()
    assert await _resolve_user_id(runner, "ai_jobs", "42") == "42"
    runner.get_publish_user_id.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_user_id_uses_single_profile_owner() -> None:
    store = SimpleNamespace(list_candidate_profile_users=AsyncMock(return_value=("42",)))
    runner = SimpleNamespace(
        get_publish_user_id=AsyncMock(return_value=None),
        get_runtime=MagicMock(return_value=SimpleNamespace(store=store)),
    )
    assert await _resolve_user_id(runner, "ai_jobs", None) == "42"


def test_select_runtime_builds_platform_specific_config_path(monkeypatch) -> None:
    monkeypatch.delenv("JOB_FTCH_RUNTIME_CONFIG_PATH", raising=False)
    _select_runtime("prod")
    assert "config/runtime.prod.yaml" in os.environ["JOB_FTCH_RUNTIME_CONFIG_PATH"]


def test_scripted_ingest_is_clean_by_default() -> None:
    assert _build_parser().parse_args([]).no_clean is False
    assert _build_parser().parse_args(["--no-clean"]).no_clean is True
    assert _build_parser().parse_args(["--clean-only"]).clean_only is True


def test_scripted_ingest_accepts_explicit_canonical_source_scope() -> None:
    args = _build_parser().parse_args(
        [
            "--source-id",
            "telegram_channel:forproducts",
            "--source-id",
            "telegram_channel:ml_jobs_kz",
        ]
    )
    assert args.source_ids == [
        "telegram_channel:forproducts",
        "telegram_channel:ml_jobs_kz",
    ]


def test_scripted_ingest_accepts_explicit_tenant_config_directory() -> None:
    args = _build_parser().parse_args(
        ["--configs-dir", "job_ftch/adapters/telegram_bot/config/tenants"]
    )
    assert args.configs_dir == Path("job_ftch/adapters/telegram_bot/config/tenants")


def test_write_report_creates_complete_json_atomically(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "report.json"

    _write_report(path, {"run_id": "run-1", "cost_usd": 0.0123})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run_id": "run-1",
        "cost_usd": 0.0123,
    }
    assert not path.with_suffix(".json.tmp").exists()


def test_clear_output_artifacts_removes_outputs_and_sidecars(tmp_path: Path) -> None:
    paths = [
        tmp_path / "jobs.json",
        tmp_path / "review.jsonl",
        tmp_path / "rejected.jsonl",
        tmp_path / "quarantine.jsonl",
    ]
    for path in paths:
        path.write_text("stale", encoding="utf-8")
    (tmp_path / "jobs.123.staging.jsonl").write_text("stale", encoding="utf-8")
    (tmp_path / "review.123.tmp.jsonl").write_text("stale", encoding="utf-8")
    settings = SimpleNamespace(
        output_path=paths[0],
        review_output_path=paths[1],
        rejected_output_path=paths[2],
        quarantine_output_path=paths[3],
    )

    assert _clear_output_artifacts(settings) == 6
    assert not any(tmp_path.iterdir())


def test_preflight_failure_writes_machine_readable_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report_path = tmp_path / "preflight.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bot_ingest.py",
            "--runtime",
            "dev",
            "--preflight",
            "--report-path",
            str(report_path),
        ],
    )
    monkeypatch.setattr("scripts.run_bot_ingest._select_runtime", lambda _runtime: None)

    def fail_preflight(coroutine: object) -> None:
        coroutine.close()  # type: ignore[union-attr]
        raise ConnectionRefusedError("db unavailable")

    monkeypatch.setattr(
        "scripts.run_bot_ingest.asyncio.run",
        fail_preflight,
    )

    assert main() == 2
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "tenant_id": "ai_jobs",
        "runtime": "dev",
        "preflight": False,
        "error": {"type": "ConnectionRefusedError", "message": "db unavailable"},
    }
