from __future__ import annotations

from job_ftch.application.graph import compile_graph, load_graph
from job_ftch.application.graph.manifests import build_run_manifest


def test_manifest_hashes_immutable_resources_without_embedding_them(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"stable_id":"a"}\n', encoding="utf-8")
    manifest = build_run_manifest(
        graph=compile_graph(load_graph("config/pipelines/evidence_v2.yaml")),
        dataset=dataset,
        seed=1,
        selected_item_ids=["a"],
        runtime={},
        shot_snapshot={},
        immutable_resources={"policy": {"threshold": 0.9}, "brief": "private text"},
    )
    assert set(manifest["immutable_resources"]) == {"brief", "policy"}
    assert "private text" not in str(manifest)
