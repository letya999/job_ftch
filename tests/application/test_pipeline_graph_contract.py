"""Phase-0 contract for the actual settings-backed pipeline graph."""

from __future__ import annotations

import pytest

from job_ftch.application.builder import _validate_pipeline_graph, build_nodes, load_profile_catalog
from job_ftch.application.dedup_settlement import (
    DedupSettlementCoordinator,
    SettlementOutcome,
    collect_settlement_participants,
)
from job_ftch.config import Settings
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "store_backend": "memory",
            "llm_backend": "heuristic",
            "source_backend": "local_fixture",
            **overrides,
        }
    )


def _graph_names(settings: Settings, llm: HeuristicLLMProvider | None = None) -> list[str]:
    catalog = load_profile_catalog(settings)
    sanitize, snapshot, nodes = build_nodes(
        settings,
        InMemoryStore(),
        llm=llm or HeuristicLLMProvider(),
        job_group_store=InMemoryJobGroupStore(),
        catalog=catalog,
        run_id="graph-contract",
        tenant_id="default",
    )
    assert snapshot is nodes[0]
    return [type(sanitize).__name__, *(type(node).__name__ for node in nodes)]


def test_default_runtime_graph_has_required_phase_zero_boundaries() -> None:
    names = _graph_names(_settings())

    assert names[0:4] == [
        "SanitizeNode",
        "SnapshotFilterNode",
        "SourceContextNode",
        "OntologySnapshotNode",
    ]
    assert names.index("OntologySnapshotNode") < names.index("ExtractionNode")
    assert names.index("SnapshotFilterNode") < names.index("ExtractionNode")
    assert names.index("RawJobnessEvidenceNode") < names.index("ExtractionNode")
    assert names.index("ExtractionNode") < names.index("JobnessEvidenceProducer")
    assert names.index("LLMRelevanceClassificationNode") < names.index("EvidenceDecisionNode")
    assert "EvidenceDecisionNode" in names
    assert "PresentableTextNode" not in names
    assert "RoutingNode" not in names
    assert "EmbeddingNode" not in names
    assert "FullExtractionNode" not in names
    assert names.index("EvidenceDecisionNode") < names.index("JobAggregationNode")
    assert "FullExtractionNode" not in names


def test_presentation_is_post_accept_enrichment_not_terminal_graph() -> None:
    class PresentableHeuristic(HeuristicLLMProvider):
        async def generate_text(self, prompt: str, *, temperature: float = 0.2) -> str:
            del prompt, temperature
            return "unused during graph construction"

    names = _graph_names(_settings(), PresentableHeuristic())
    assert "PresentableTextNode" not in names


def test_reranker_thresholds_without_a_producer_are_invalid() -> None:
    settings = _settings(routing_reranker_accept_threshold=0.7)

    with pytest.raises(ValueError, match="resolver-only"):
        _validate_pipeline_graph(settings, [])


def test_reranker_thresholds_are_not_silent_runtime_fallbacks() -> None:
    with pytest.raises(ValueError, match="resolver-only"):
        _validate_pipeline_graph(_settings(routing_reranker_accept_threshold=0.7), [])


def test_segmentation_is_explicit_opt_in_graph_boundary() -> None:
    names = _graph_names(_settings(pipeline_candidate_segmentation_enabled=True))
    assert names.index("SourceContextNode") < names.index("CandidateSegmentationNode")
    assert names.index("CandidateSegmentationNode") < names.index("GarbageFilterNode")


@pytest.mark.anyio
async def test_deferred_item_is_reprocessable_after_replay() -> None:
    """A DEFERRED item must not be permanently consumed as a duplicate.

    This is the key regression test for the settlement-ownership fix.
    Before the fix, GraphExecutor.run_many committed dedup claims for ALL
    seen items including DEFERRED ones, permanently poisoning replay.
    """
    from job_ftch.application.graph import GraphCompiler
    from job_ftch.application.graph.contracts import GraphSpec, NodeSpec
    from job_ftch.application.graph.executor import GraphExecutor
    from job_ftch.application.graph.pipeline_stage import GraphPipelineStage
    from job_ftch.domain import RawItem, SourceKind
    from job_ftch.nodes.dedup import DedupNode

    store = InMemoryStore()
    dedup = DedupNode(store, defer_commit=True)

    class _Pass:
        async def process(self, item: object) -> object:
            return item

    class _DeferResult:
        routing_decision = "accept"
        work_state = "deferred"

    class _DeferTerminal:
        async def process(self, item: object) -> _DeferResult:
            return _DeferResult()

    graph = GraphCompiler().compile(
        GraphSpec(
            "deferred-replay",
            "1",
            (
                NodeSpec("sanitize", "sanitize", effect="gate"),
                NodeSpec("dedup", "dedup", effect="gate", after=("sanitize",)),
                NodeSpec("extract", "extraction", after=("dedup",)),
                NodeSpec("normalization", "job_normalization", after=("extract",)),
                NodeSpec(
                    "terminal",
                    "legacy_routing",
                    effect="terminal_decision",
                    after=("normalization",),
                ),
            ),
        )
    )
    executor = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": dedup,
            "extract": _Pass(),
            "normalization": _Pass(),
            "terminal": _DeferTerminal(),
        },
    )
    stage = GraphPipelineStage(executor)

    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="replay-target",
        text="ML Engineer - build recommendation systems",
    )

    # --- Run 1: item gets DEFERRED ---
    reports = await executor.run_many(item)
    assert reports[0].status == "DEFERRED"

    # Settle as RELEASE (what Pipeline does for DEFERRED)
    coordinator = DedupSettlementCoordinator(collect_settlement_participants([stage]))
    await coordinator.settle(item.stable_id, SettlementOutcome.RELEASE)

    # --- Run 2 (replay): item must pass through dedup again ---
    dedup2 = DedupNode(store, defer_commit=True)
    executor2 = GraphExecutor(
        graph,
        factories={
            "sanitize": _Pass(),
            "dedup": dedup2,
            "extract": _Pass(),
            "normalization": _Pass(),
            "terminal": _DeferTerminal(),
        },
    )
    reports2 = await executor2.run_many(item)
    assert reports2[0].status == "DEFERRED"
