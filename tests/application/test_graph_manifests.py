from __future__ import annotations

from pathlib import Path

from job_ftch.application.graph import compile_graph, load_graph
from job_ftch.application.graph.manifests import build_run_manifest


def test_run_manifest_is_reproducible_and_does_not_copy_shot_text() -> None:
    graph = compile_graph(load_graph("config/pipelines/current_compat.yaml"))
    manifest = build_run_manifest(
        graph=graph,
        dataset=Path("fixtures/dataset/eval_dataset.jsonl"),
        seed=42,
        selected_item_ids=["b", "a"],
        runtime={"tenant_id": "tenant", "model": "model"},
        shot_snapshot={"hash": "snapshot", "positive_count": 2, "negative_count": 3},
    )
    assert len(manifest["dataset"]["sha256"]) == 64
    assert manifest["selected_item_count"] == 2
    assert "text" not in manifest["shots"]
    assert len(manifest["git"]["dirty_diff_sha256"]) == 64
    assert manifest["environment"]["python"]
