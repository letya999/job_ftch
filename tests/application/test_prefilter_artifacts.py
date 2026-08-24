from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from job_ftch.application.prefilter_artifacts import (
    apply_promoted_prefilter_to_graph,
    evaluate_prefilter,
    get_prefilter_status,
    list_prefilter_artifacts,
    mark_prefilter_dirty,
    prefilter_dir,
    promote_prefilter,
    resolve_prefilter_model_path,
    rollback_prefilter,
    train_prefilter,
    validate_prefilter_dataset,
)


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(store_path=tmp_path / "t1" / "store.db")


def _write_dataset(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    return path


def _write_artifact(path: Path, *, retention: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_version": "tfidf-logreg-v1",
                "created_at": "2026-08-18T00:00:00+00:00",
                "vocabulary": {},
                "idf": [],
                "coef": [],
                "intercept": 0.0,
                "training": {"n_rows": 2200, "n_positive": 250},
                "metrics": {
                    "target_threshold": 0.3,
                    "holdout_positive_retention": retention,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_validate_dataset_reports_undersized(tmp_path: Path) -> None:
    dataset = _write_dataset(
        tmp_path / "tiny.jsonl",
        [
            {"stable_id": "p1", "text": "LLM engineer", "relevant": 1},
            {"stable_id": "n1", "text": "accountant", "relevant": 0},
            {"stable_id": "n2", "text": "salesperson", "label": "negative"},
        ],
    )
    stats = validate_prefilter_dataset(str(dataset))
    assert stats["ok"] is False
    assert stats["n_rows"] == 3
    assert stats["n_positive"] == 1
    assert stats["n_negative"] == 2
    assert stats["production_ready"] is False


def test_dirty_status_promote_and_rollback(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    status = get_prefilter_status(settings, tenant_id="t1")
    assert status["dirty"] is True
    assert status["current_artifact_id"] is None

    mark_prefilter_dirty(settings)
    assert get_prefilter_status(settings, tenant_id="t1")["dirty"] is True

    root = prefilter_dir(settings)
    first = _write_artifact(root / "artifacts" / "art-a.json", retention=0.95)
    second = _write_artifact(root / "artifacts" / "art-b.json", retention=0.91)
    del first, second

    promoted = promote_prefilter(settings, tenant_id="t1", artifact_id="art-a")
    assert promoted["ok"] is True
    assert get_prefilter_status(settings, tenant_id="t1")["dirty"] is False
    assert get_prefilter_status(settings, tenant_id="t1")["current_artifact_id"] == "art-a"

    blocked = promote_prefilter(settings, tenant_id="t1", artifact_id="art-b")
    # art-b also passes gate; promote should succeed and keep previous
    assert blocked["ok"] is True
    assert blocked["previous_artifact_id"] == "art-a"

    rolled = rollback_prefilter(settings, tenant_id="t1")
    assert rolled["ok"] is True
    listed = list_prefilter_artifacts(settings, tenant_id="t1")
    assert listed["count"] == 2
    assert listed["current_artifact_id"] == "art-a"
    status = get_prefilter_status(settings, tenant_id="t1")
    assert status["using_promoted"] is True
    assert status["active_model_path"].endswith("current.json")
    assert status["dirty"] is False
    assert resolve_prefilter_model_path(settings).endswith("current.json")


def test_promote_requires_eval_gate(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = prefilter_dir(settings)
    _write_artifact(root / "artifacts" / "art-bad.json", retention=0.4)
    result = promote_prefilter(settings, tenant_id="t1", artifact_id="art-bad")
    assert result["error"] == "gate_failed"
    forced = promote_prefilter(
        settings,
        tenant_id="t1",
        artifact_id="art-bad",
        require_gate_pass=False,
    )
    assert forced["ok"] is True


def test_train_dry_run_does_not_write(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    dataset = _write_dataset(
        tmp_path / "tiny.jsonl",
        [{"stable_id": "p1", "text": "x", "relevant": 1}],
    )
    result = train_prefilter(
        settings,
        tenant_id="t1",
        dataset_id_or_path=str(dataset),
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["would_write"] is False
    assert result["dataset"]["ok"] is False
    assert list(prefilter_dir(settings).joinpath("artifacts").glob("*.json")) == []

    written = train_prefilter(
        settings,
        tenant_id="t1",
        dataset_id_or_path=str(dataset),
        dry_run=False,
    )
    assert written["ok"] is False
    assert written["error"] == "dataset_not_ready"


def test_resolve_falls_back_to_fixture_without_current(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert resolve_prefilter_model_path(settings, "fixtures/prefilter/tfidf_logreg_v1.json") == (
        "fixtures/prefilter/tfidf_logreg_v1.json"
    )
    status = get_prefilter_status(settings, tenant_id="t1")
    assert status["using_promoted"] is False
    assert not (tmp_path / "t1" / "prefilter").exists()


def test_apply_promoted_prefilter_to_graph(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = prefilter_dir(settings)
    _write_artifact(root / "artifacts" / "art-g.json", retention=0.95)
    promote_prefilter(settings, tenant_id="t1", artifact_id="art-g")

    class _Node:
        def __init__(self) -> None:
            self.node = "tfidf_logreg_prefilter"
            self.params = {"model_path": "fixtures/prefilter/tfidf_logreg_v1.json"}

    class _Graph:
        def __init__(self) -> None:
            self.spec = SimpleNamespace(nodes=(_Node(),))

    graph = _Graph()
    applied = apply_promoted_prefilter_to_graph(settings, graph)
    assert applied is not None
    assert applied.endswith("current.json")
    assert graph.spec.nodes[0].params["model_path"].endswith("current.json")


@pytest.mark.asyncio
async def test_evaluate_uses_stored_metrics(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    root = prefilter_dir(settings)
    _write_artifact(root / "artifacts" / "art-eval.json", retention=0.96)
    result = evaluate_prefilter(settings, tenant_id="t1", artifact_id="art-eval")
    assert result["ok"] is True
    assert result["gate_pass"] is True
    assert result["stored_metrics"]["holdout_positive_retention"] == 0.96
