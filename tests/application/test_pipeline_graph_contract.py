"""Phase-0 contract for the actual settings-backed pipeline graph."""

from __future__ import annotations

import pytest

from job_ftch.application.builder import _validate_pipeline_graph, build_nodes, load_profile_catalog
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
