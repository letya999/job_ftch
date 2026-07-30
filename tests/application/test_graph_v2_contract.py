from __future__ import annotations

from dataclasses import replace

import pytest

from job_ftch.application.graph.compiler import compile_graph
from job_ftch.application.graph.loader import load_graph


def test_v2_graph_is_a_linear_typed_spine() -> None:
    graph = compile_graph(load_graph("config/pipelines/evidence_v2.yaml"))

    order = graph.execution_order
    assert order[0] == "sanitize"
    assert order.index("segmentation") < order.index("dedup") < order.index("extraction")
    assert order.index("evidence") < order.index("relevance_judge") < order.index("decision")
    assert order.index("decision") < order.index("aggregation") < order.index("enrichment")


def test_compact_v2_graph_selects_responsibility_evidence_schema() -> None:
    graph = compile_graph(load_graph("config/pipelines/evidence_v2_compact.yaml"))

    judge = next(node for node in graph.spec.nodes if node.id == "relevance_judge")
    assert judge.params["call_policy"] == "force_all"
    assert judge.params["classification_mode"] == "compact_evidence"


def test_postaccept_compact_graph_uses_zero_llm_triage_boundary() -> None:
    graph = compile_graph(load_graph("config/pipelines/evidence_v2_compact_postaccept.yaml"))

    extraction = next(node for node in graph.spec.nodes if node.id == "extraction")
    assert extraction.params["extraction_mode"] == "structured_or_heuristic"
    judge = next(node for node in graph.spec.nodes if node.id == "relevance_judge")
    assert judge.params["max_per_run"] == 600
    assert graph.execution_order[0] == "sanitize"
    assert graph.execution_order.index("decision") < graph.execution_order.index("enrichment")


def test_v2_graph_rejects_pseudo_dag_edges() -> None:
    spec = load_graph("config/pipelines/evidence_v2.yaml")
    pseudo_dag = replace(
        spec,
        nodes=(
            *spec.nodes[:1],
            replace(
                spec.nodes[1],
                execution="parallel",
                start_after=("sanitize",),
            ),
            *spec.nodes[2:],
        ),
    )

    with pytest.raises(ValueError, match="linear typed spine"):
        compile_graph(pseudo_dag)


def test_v2_graph_requires_decision_node_as_terminal_owner() -> None:
    spec = load_graph("config/pipelines/evidence_v2.yaml")
    wrong_terminal = replace(
        spec,
        nodes=tuple(
            replace(node, node="legacy_routing") if node.id == "decision" else node
            for node in spec.nodes
        ),
    )

    with pytest.raises(ValueError, match="DecisionNode"):
        compile_graph(wrong_terminal)


def test_compiler_rejects_disabled_dependency_and_disconnected_terminal() -> None:
    from job_ftch.application.graph.contracts import GraphSpec, NodeSpec

    with pytest.raises(ValueError, match="dependency is disabled"):
        compile_graph(
            GraphSpec(
                "disabled",
                "1",
                (
                    NodeSpec("sanitize", "sanitize", effect="gate"),
                    NodeSpec("context", "source_context", enabled=False, after=("sanitize",)),
                    NodeSpec("extraction", "extraction", after=("context",)),
                    NodeSpec("normalization", "job_normalization", after=("extraction",)),
                    NodeSpec("evidence", "evidence_fanout", after=("normalization",)),
                    NodeSpec(
                        "decision", "decision", after=("evidence",), effect="terminal_decision"
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="reachable"):
        compile_graph(
            GraphSpec(
                "disconnected",
                "1",
                (
                    NodeSpec("sanitize", "sanitize", effect="gate"),
                    NodeSpec("extraction", "extraction"),
                    NodeSpec("normalization", "job_normalization", after=("extraction",)),
                    NodeSpec("evidence", "evidence_fanout", after=("normalization",)),
                    NodeSpec(
                        "decision", "decision", after=("evidence",), effect="terminal_decision"
                    ),
                ),
            )
        )
