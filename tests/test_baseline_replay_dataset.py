from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module() -> object:
    path = Path("scripts/eval/baseline_report.py")
    spec = importlib.util.spec_from_file_location("baseline_report", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_replay_fixture_preserves_production_envelope() -> None:
    module = _module()
    rows = module.load_replay_rows(Path("fixtures/dataset/raw_replay.jsonl"))
    report = module.build_report(rows)

    assert report["observations_without_url"] == 1
    assert report["source_coverage"] == {"career_site": 1, "telegram_comment": 1}
    assert report["pending_human_labels"] > 0


def test_raw_replay_rejects_tampered_payload_hash(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "bad.jsonl"
    path.write_text(
        Path("fixtures/dataset/raw_replay.jsonl")
        .read_text(encoding="utf-8")
        .replace("RAG-проекта", "tampered", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash"):
        module.load_replay_rows(path)
