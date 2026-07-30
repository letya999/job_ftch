from pathlib import Path

from job_ftch.application.graph import GraphCompiler, load_graph

ROOT = Path(__file__).parents[2]


def test_compact_prefilter_graph_compiles_and_wires_tfidf_logreg_prefilter() -> None:
    path = ROOT / "config" / "pipelines" / "evidence_v2_compact_prefilter.yaml"
    graph = GraphCompiler().compile(load_graph(path))

    # Execution order check
    order = graph.execution_order
    assert "dedup" in order
    assert "tfidf_logreg_prefilter" in order
    assert "semantic" in order

    dedup_idx = order.index("dedup")
    prefilter_idx = order.index("tfidf_logreg_prefilter")
    semantic_idx = order.index("semantic")

    assert dedup_idx < prefilter_idx < semantic_idx

    # Effect check
    nodes = {n.id: n for n in graph.spec.nodes}
    assert nodes["tfidf_logreg_prefilter"].effect == "gate"

    # Graph hash difference check
    postaccept_path = ROOT / "config" / "pipelines" / "evidence_v2_compact_postaccept.yaml"
    postaccept_graph = GraphCompiler().compile(load_graph(postaccept_path))

    assert graph.graph_hash != postaccept_graph.graph_hash
