from pathlib import Path

import pytest

from job_ftch.application.graph import compile_graph, load_graph
from job_ftch.nodes.routing import RoutingNode
from job_ftch.nodes.uncertainty_router import UncertaintyRouterNode

ROOT = Path(__file__).parents[2]
EXPERIMENTS = (
    ROOT / "config" / "pipelines" / "asis_legacy_best.yaml",
    *sorted((ROOT / "config" / "pipelines").glob("experiment_h1[1-6]_*.yaml")),
)


@pytest.mark.parametrize("path", EXPERIMENTS, ids=lambda path: path.stem)
def test_legacy_experiment_graph_is_compilable(path: Path) -> None:
    graph = compile_graph(load_graph(path))
    terminal = [node for node in graph.spec.nodes if node.effect == "terminal_decision"]
    assert len(terminal) == 1
    assert graph.spec.nodes[0].node == "sanitize"
    assert graph.graph_hash
    bgem3 = next(node for node in graph.spec.nodes if node.node == "bgem3_embed")
    assert bgem3.timeout_ms >= 180_000
    assert all(node.node != "final_group_update" for node in graph.spec.nodes)


def test_h15_changes_only_post_decision_presentation_order() -> None:
    control = compile_graph(load_graph(ROOT / "config" / "pipelines" / "asis_legacy_best.yaml"))
    h15 = compile_graph(
        load_graph(ROOT / "config" / "pipelines" / "experiment_h15_post_decision_presentation.yaml")
    )

    assert {node.node for node in h15.spec.nodes} == {node.node for node in control.spec.nodes}
    assert h15.execution_order.index("legacy_routing") < h15.execution_order.index(
        "presentable_text"
    )
    assert control.execution_order.index("presentable_text") < control.execution_order.index(
        "legacy_routing"
    )


def test_h14_changes_only_dedup_mode() -> None:
    control = compile_graph(load_graph(ROOT / "config" / "pipelines" / "asis_legacy_best.yaml"))
    h14 = compile_graph(
        load_graph(ROOT / "config" / "pipelines" / "experiment_h14_late_canonicalization.yaml")
    )

    assert h14.execution_order == control.execution_order
    h14_dedup = next(node for node in h14.spec.nodes if node.node == "dedup")
    assert h14_dedup.shadow is True


@pytest.mark.asyncio
async def test_uncertainty_router_only_marks_llm_zone(make_job_record) -> None:
    node = UncertaintyRouterNode(low_threshold=0.2, high_threshold=0.5)
    negative = await node.process(make_job_record(relevance_score=0.1, quality_score=0.1))
    uncertain = await node.process(make_job_record(relevance_score=0.3))
    positive = await node.process(make_job_record(relevance_score=0.8, quality_score=0.8))
    assert negative.metadata["uncertainty_zone"] == "consistent_negative"
    assert negative.metadata["needs_llm_review"] is False
    assert negative.metadata["uncertainty_recommendation"] == "reject"
    assert uncertain.metadata["uncertainty_zone"] == "disagreement"
    assert uncertain.metadata["needs_llm_review"] is True
    assert uncertain.metadata["uncertainty_recommendation"] is None
    assert positive.metadata["uncertainty_zone"] == "consistent_positive"
    assert positive.metadata["uncertainty_recommendation"] == "accept"


@pytest.mark.asyncio
async def test_routing_honours_uncertainty_recommendation(make_job_record) -> None:
    accepted = await RoutingNode().process(
        make_job_record(metadata={"uncertainty_recommendation": "accept"})
    )
    rejected = await RoutingNode().process(
        make_job_record(metadata={"uncertainty_recommendation": "reject"})
    )
    assert accepted.routing_decision.value == "accept"
    assert rejected.routing_decision.value == "reject"


def test_h11_h12_h13_keep_control_prefix_until_their_declared_ablation() -> None:
    control = compile_graph(load_graph(ROOT / "config" / "pipelines" / "asis_legacy_best.yaml"))
    control_nodes = [node.node for node in control.spec.nodes]
    common_prefix = control_nodes[:12]
    for filename in (
        "experiment_h11_no_late_scoring.yaml",
        "experiment_h12_uncertainty_router.yaml",
        "experiment_h13_full_after_decision.yaml",
    ):
        graph = compile_graph(load_graph(ROOT / "config" / "pipelines" / filename))
        nodes = [node.node for node in graph.spec.nodes]
        assert nodes[: len(common_prefix)] == common_prefix

    h11 = compile_graph(
        load_graph(ROOT / "config" / "pipelines" / "experiment_h11_no_late_scoring.yaml")
    )
    h11_nodes = [node.node for node in h11.spec.nodes]
    assert h11_nodes[12:] == [
        "extraction",
        "extraction_validation",
        "job_normalization",
        "skill_normalization",
        "location_work_mode_normalization",
        "compensation",
        "lifecycle",
        "llm_relevance",
        "presentable_text",
        "legacy_routing",
        "aggregation",
    ]

    h12 = compile_graph(
        load_graph(ROOT / "config" / "pipelines" / "experiment_h12_uncertainty_router.yaml")
    )
    h12_nodes = [node.node for node in h12.spec.nodes]
    assert h12_nodes == [*control_nodes[:25], "uncertainty_router", *control_nodes[25:]]

    h13 = compile_graph(
        load_graph(ROOT / "config" / "pipelines" / "experiment_h13_full_after_decision.yaml")
    )
    h13_nodes = [node.node for node in h13.spec.nodes]
    assert h13_nodes == [
        *control_nodes[:12],
        "triage_extraction",
        *control_nodes[13:-1],
        "full_extraction",
        "aggregation",
    ]
