from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.graph import GraphCompiler, load_graph
from job_ftch.application.graph.executor import GraphExecutor
from job_ftch.application.graph.inspection import print_graph
from job_ftch.domain import RawItem, TriageRejectionReason

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "name",
    (
        "historical_best",
        "current_compat",
        "evidence_v2",
        "full_compat",
        "experiment_weighted_signals",
        "experiment_ablate_llm_signal",
        "experiment_parallel_bgem3",
        "experiment_shadow_prefilter",
        "experiment_current_shadow",
    ),
)
def test_presets_compile_without_loading_models_or_dataset(name: str) -> None:
    graph = GraphCompiler().compile(load_graph(ROOT / "config" / "pipelines" / f"{name}.yaml"))
    assert len(graph.graph_hash) == 64
    assert graph.execution_order[0] == "sanitize"


def test_invalid_graph_is_rejected_before_factory_loading() -> None:
    spec = load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    nodes = list(spec.nodes)
    nodes[0] = nodes[0].__class__(id="sanitize", node="sanitize", enabled=False, effect="gate")
    with pytest.raises(ValueError, match="SanitizeNode"):
        GraphCompiler().compile(
            spec.__class__(spec.name, spec.version, tuple(nodes), spec.metadata)
        )


def test_graph_hash_is_stable_and_dot_is_inspectable() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )
    again = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )
    assert graph.graph_hash == again.graph_hash
    assert '"sanitize" -> "snapshot"' in print_graph(graph, "dot")


def test_historical_preset_keeps_verified_recipe_parameters() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "historical_best.yaml")
    )
    nodes = {node.id: node for node in graph.spec.nodes}
    assert nodes["bgem3"].params["model"] == "BAAI/bge-m3"
    assert nodes["semantic_prefilter"].params["dense_margin_threshold"] == 0.05
    assert nodes["parallel_scoring"].params["margin_k"] == 20
    assert nodes["legacy_routing"].effect == "terminal_decision"


def test_exact_86bc192_legacy_preset_preserves_its_checkpoint_order() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "historical_86bc192.yaml")
    )
    assert graph.execution_order.index("aggregation") < graph.execution_order.index(
        "legacy_routing"
    )
    assert graph.execution_order[-1] == "final_group_update"


def test_graph_params_are_applied_to_runtime_factories() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "historical_best.yaml")
    )

    class Configurable:
        def __init__(self, terminal: bool = False) -> None:
            self.received: dict[str, object] | None = None
            self.terminal = terminal

        def configure_graph_params(self, params: dict[str, object]) -> None:
            self.received = params

        async def process(self, item: object) -> object:
            return {"decision": "reject"} if self.terminal else item

    factories = {
        node.id: Configurable(terminal=node.effect == "terminal_decision")
        for node in graph.spec.nodes
    }
    executor = GraphExecutor.from_nodes(graph, [], extra=factories)
    asyncio.run(executor.run("raw"))
    assert factories["bgem3"].received == {"model": "BAAI/bge-m3"}
    assert factories["parallel_scoring"].received == {"margin_k": 20}


def test_unknown_graph_parameter_fails_at_compile_time() -> None:
    spec = load_graph(ROOT / "config" / "pipelines" / "historical_best.yaml")
    nodes = list(spec.nodes)
    target = nodes.index(next(node for node in nodes if node.id == "parallel_scoring"))
    node = nodes[target]
    nodes[target] = node.__class__(**{**node.__dict__, "params": {"unknown": 1}})
    with pytest.raises(ValueError, match="unsupported graph parameters"):
        GraphCompiler().compile(
            spec.__class__(spec.name, spec.version, tuple(nodes), spec.metadata)
        )


def test_authority_mode_controls_effect_and_shadow() -> None:
    spec = load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    nodes = list(spec.nodes)
    target = nodes.index(next(node for node in nodes if node.id == "snapshot"))
    node = nodes[target]
    nodes[target] = node.__class__(**{**node.__dict__, "authority": "shadow"})
    compiled = GraphCompiler().compile(
        spec.__class__(spec.name, spec.version, tuple(nodes), spec.metadata)
    )
    snapshot = next(node for node in compiled.spec.nodes if node.id == "snapshot")
    assert snapshot.effect == "gate"
    assert snapshot.shadow is True


def test_executor_runs_synthetic_sequential_fixture() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Node:
        async def process(self, item: str) -> str:
            return item + ".x"

    class Terminal:
        async def process(self, item: str) -> dict[str, str]:
            return {"decision": "accept"}

    factories = {node.id: Node() for node in graph.spec.nodes}
    factories["decision"] = Terminal()
    result = asyncio.run(GraphExecutor(graph, factories=factories).run("raw"))
    assert result.status == "ACCEPT"
    assert result.item == {"decision": "accept"}


def test_executor_does_not_silently_replace_missing_runtime_factory() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )
    with pytest.raises(RuntimeError, match="no runnable factory"):
        asyncio.run(GraphExecutor(graph).run("raw"))


def test_compiler_rejects_sequential_type_mismatch() -> None:
    from job_ftch.application.graph.contracts import GraphSpec, NodeSpec

    spec = GraphSpec(
        "bad",
        "1",
        (
            NodeSpec("sanitize", "sanitize", effect="gate"),
            NodeSpec("decision", "decision", effect="terminal_decision", after=("sanitize",)),
        ),
    )
    with pytest.raises(ValueError, match="cannot feed"):
        GraphCompiler().compile(spec)


def test_terminal_result_controls_report_status() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Pass:
        def configure_graph_params(self, _params: dict[str, object]) -> None:
            return None

        async def process(self, item: str) -> str:
            return item

    class Reject:
        async def process(self, item: str) -> dict[str, str]:
            return {"decision": "reject"}

    factories = {node.id: Pass() for node in graph.spec.nodes}
    factories["decision"] = Reject()
    result = asyncio.run(GraphExecutor(graph, factories=factories).run("raw"))
    assert result.status == "REJECT"
    assert result.node_events["decision"]["outcome"] == "pass"
    assert result.node_events["decision"]["terminal_status"] == "REJECT"
    assert result.node_events["decision"]["terminal_reasons"] == []


def test_terminal_reasons_are_preserved_in_execution_trace() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Pass:
        def configure_graph_params(self, _params: dict[str, object]) -> None:
            return None

        async def process(self, item: str) -> str:
            return item

    class Deferred:
        async def process(self, item: str) -> object:
            return type(
                "Result",
                (),
                {
                    "routing_decision": None,
                    "work_state": "deferred",
                    "reasons": ("jobness_unknown",),
                },
            )()

    factories = {node.id: Pass() for node in graph.spec.nodes}
    factories["decision"] = Deferred()
    result = asyncio.run(GraphExecutor(graph, factories=factories).run("raw"))

    assert result.status == "DEFERRED"
    assert result.node_events["decision"]["terminal_reasons"] == ["jobness_unknown"]


def test_explicit_drop_reason_and_diagnostics_are_preserved_in_execution_trace() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Pass:
        async def process(self, item: RawItem) -> RawItem:
            return item

    class Drop:
        async def process(self, item: RawItem) -> RawItem:
            dropped = item.model_copy(
                update={
                    "metadata": {
                        **item.metadata,
                        "relevance_prefilter_score": 0.12,
                        "relevance_prefilter_threshold": 0.35,
                        "relevance_prefilter_decision": "drop",
                        "relevance_prefilter_model_version": "test-model",
                    }
                }
            )
            raise RawItemDropped(
                reason=TriageRejectionReason.LOW_RELEVANCE_PREFILTER,
                details="score below threshold",
                item=dropped,
                stage="TfidfLogregRelevancePrefilterNode",
            )

    factories = {node.id: Pass() for node in graph.spec.nodes}
    factories["snapshot"] = Drop()
    raw = RawItem(source_kind="debug", source_name="debug", external_id="1", text="irrelevant")
    result = asyncio.run(GraphExecutor(graph, factories=factories).run(raw))

    event = result.node_events["snapshot"]
    assert result.status == "REJECT"
    assert event["outcome"] == "drop"
    assert event["reason"] == "low_relevance_prefilter"
    assert event["relevance_prefilter_score"] == 0.12
    assert event["relevance_prefilter_threshold"] == 0.35
    assert event["relevance_prefilter_decision"] == "drop"


def test_terminal_preserves_typed_evidence_for_offline_calibration() -> None:
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Pass:
        async def process(self, item: str) -> str:
            return item

    class TypedValue:
        def __init__(self, value: dict[str, object]) -> None:
            self.value = value

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return self.value

    class Decision:
        routing_decision = "reject"
        work_state = "terminal"
        reasons = ()
        assessed_job = type(
            "Assessed",
            (),
            {
                "record": "raw",
                "policy_version": "evidence-v2",
                "evidence": (TypedValue({"claim": "profile_relevance"}),),
                "assessments": (TypedValue({"belief_true": 0.2}),),
                "degradation_reasons": ("producer_timeout",),
            },
        )()

    class Terminal:
        async def process(self, item: str) -> Decision:
            return Decision()

    factories = {node.id: Pass() for node in graph.spec.nodes}
    factories["decision"] = Terminal()
    result = asyncio.run(GraphExecutor(graph, factories=factories).run("raw"))

    assert result.artifacts["typed_evidence"] == {
        "policy_version": "evidence-v2",
        "atoms": [{"claim": "profile_relevance"}],
        "assessments": [{"belief_true": 0.2}],
        "degradation_reasons": ["producer_timeout"],
    }


def test_current_compat_binds_real_builder_nodes() -> None:
    from job_ftch.application.builder import build_nodes, load_profile_catalog
    from job_ftch.config import Settings
    from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore

    settings = Settings.model_validate(
        {"store_backend": "memory", "llm_backend": "heuristic", "source_backend": "local_fixture"}
    )
    catalog = load_profile_catalog(settings)
    sanitize, _, nodes = build_nodes(
        settings,
        InMemoryStore(),
        HeuristicLLMProvider(),
        InMemoryJobGroupStore(),
        catalog=catalog,
        run_id="graph-bind",
        tenant_id="default",
    )
    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )
    executor = GraphExecutor.from_nodes(graph, [sanitize, *nodes])
    assert set(node.id for node in graph.spec.nodes) == set(executor.factories)

    from job_ftch.domain import RawItem, SourceKind

    report = asyncio.run(
        executor.run(
            RawItem(
                source_kind=SourceKind.DEBUG,
                source_name="synthetic",
                external_id="graph-smoke",
                url="https://example.test/job",
                text="Python developer vacancy remote",
            )
        )
    )
    assert report.status in {"ACCEPT", "REVIEW", "REJECT", "DEFERRED"}


def test_runtime_context_binds_by_graph_id_without_class_name_lookup() -> None:
    from job_ftch.application.graph import RuntimeContext

    graph = GraphCompiler().compile(
        load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    )

    class Pass:
        def configure_graph_params(self, _params: dict[str, object]) -> None:
            return None

        async def process(self, item: str) -> str:
            return item

    class Terminal:
        async def process(self, item: str) -> dict[str, str]:
            return {"decision": "accept"}

    instances = {node.id: Pass() for node in graph.spec.nodes}
    instances["decision"] = Terminal()
    executor = GraphExecutor.from_runtime_context(graph, RuntimeContext(node_instances=instances))
    report = asyncio.run(executor.run("raw"))
    assert report.status == "ACCEPT"


def test_post_accept_lane_runs_only_after_accept() -> None:
    from job_ftch.application.graph import RuntimeContext
    from job_ftch.application.graph.contracts import NodeSpec

    base = load_graph(ROOT / "config" / "pipelines" / "current_compat.yaml")
    spec = base.__class__(
        base.name,
        base.version,
        base.nodes
        + (
            NodeSpec(
                "post",
                "post_accept_enrichment",
                execution="post_accept",
                effect="side_effect",
                after=("decision",),
            ),
        ),
        base.metadata,
        base.resources,
    )
    graph = GraphCompiler().compile(spec)

    class Pass:
        def configure_graph_params(self, _params: dict[str, object]) -> None:
            return None

        async def process(self, item: object) -> object:
            return item

    class Accept:
        async def process(self, item: object) -> dict[str, str]:
            return {"decision": "accept"}

    instances = {node.id: Pass() for node in graph.spec.nodes}
    instances["decision"] = Accept()
    report = asyncio.run(
        GraphExecutor.from_runtime_context(graph, RuntimeContext(node_instances=instances)).run(
            "raw"
        )
    )
    assert report.status == "ACCEPT"
    assert report.node_events["post"]["outcome"] == "pass"
