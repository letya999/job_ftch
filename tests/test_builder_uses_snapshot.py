"""Contract test: PipelineBuilder.build_nodes() includes SnapshotFilterNode
as the 2nd stage when run_id is supplied.

This is the single-tenant guarantee from ADR-036 + Wave 3 commit
9cf44dd ("feat(builder): SnapshotFilterNode as 2nd stage in build_nodes").
The test pins the contract so a future refactor of build_nodes cannot
silently drop the snapshot filter again.
"""

from __future__ import annotations

import pytest

from job_ftch.application.builder import PipelineBuilder, build_nodes
from job_ftch.application.registry import load_extensions
from job_ftch.config import Settings
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.nodes.snapshot_filter import SnapshotFilterNode


@pytest.fixture(autouse=True)
def _ensure_extensions_loaded() -> None:
    load_extensions()


def _settings_stub() -> Settings:
    return Settings.model_validate(
        {
            "store_backend": "memory",
            "llm_backend": "heuristic",
            "source_backend": "local_fixture",
        }
    )


def test_build_nodes_includes_snapshot_filter_when_run_id_set() -> None:
    settings = _settings_stub()
    store = InMemoryStore()

    from job_ftch.application.builder import load_profile_catalog

    catalog = load_profile_catalog(settings)

    sanitize, snapshot, nodes = build_nodes(
        settings,
        store,
        llm=__import__(
            "job_ftch.infrastructure.llm.heuristic", fromlist=["HeuristicLLMProvider"]
        ).HeuristicLLMProvider(),
        job_group_store=__import__(
            "job_ftch.infrastructure.stores.job_group_store",
            fromlist=["InMemoryJobGroupStore"],
        ).InMemoryJobGroupStore(),
        catalog=catalog,
        run_id="test-run-001",
        tenant_id="default",
    )
    assert isinstance(sanitize, SanitizeNode)
    assert isinstance(snapshot, SnapshotFilterNode)
    # run_id and tenant_id are stored under the underscore-prefixed
    # attribute names; Pipeline accesses them via getattr() with a
    # default for the same reason.
    assert snapshot._run_id == "test-run-001"
    assert snapshot._tenant_id == "default"
    # The snapshot filter is the first element of `nodes`, so it runs
    # second overall (after SanitizeNode).
    assert nodes[0] is snapshot


def test_build_nodes_omits_snapshot_filter_when_run_id_is_none() -> None:
    settings = _settings_stub()
    store = InMemoryStore()
    from job_ftch.application.builder import load_profile_catalog

    catalog = load_profile_catalog(settings)

    sanitize, snapshot, nodes = build_nodes(
        settings,
        store,
        llm=__import__(
            "job_ftch.infrastructure.llm.heuristic", fromlist=["HeuristicLLMProvider"]
        ).HeuristicLLMProvider(),
        job_group_store=__import__(
            "job_ftch.infrastructure.stores.job_group_store",
            fromlist=["InMemoryJobGroupStore"],
        ).InMemoryJobGroupStore(),
        catalog=catalog,
        run_id=None,
    )
    assert isinstance(sanitize, SanitizeNode)
    assert snapshot is None
    # SourceContextNode (or similar) becomes the first processing node when snapshots are off.
    assert not isinstance(nodes[0], SnapshotFilterNode)


def test_pipeline_builder_routes_snapshot_filter_to_pipeline() -> None:
    """The PipelineBuilder build() pulls the snapshot filter out of the
    stages list and passes it to Pipeline(snapshot_filter=...)."""
    from job_ftch.application.pipeline import Pipeline
    from job_ftch.domain.source_spec import LocalFixtureSpec
    from job_ftch.sinks.null_sink import NullSink

    builder = PipelineBuilder()
    store = InMemoryStore()
    builder.store(store)
    builder.source(LocalFixtureSpec(path="fixtures/debug/raw_items.json"))
    builder.stage(SanitizeNode())
    builder.sink(NullSink())
    builder.with_snapshot_filter(run_id="run-xyz", tenant_id="default")

    pipeline = builder.build()
    assert isinstance(pipeline, Pipeline)
    assert pipeline._snapshot_filter is not None  # type: ignore[attr-defined]
    assert pipeline._snapshot_filter._run_id == "run-xyz"  # type: ignore[attr-defined]
    assert pipeline._source_run_id == "run-xyz"  # type: ignore[attr-defined]


def test_pipeline_builder_without_snapshot_filter_has_none() -> None:
    from job_ftch.domain.source_spec import LocalFixtureSpec
    from job_ftch.sinks.null_sink import NullSink

    builder = PipelineBuilder()
    store = InMemoryStore()
    builder.store(store)
    builder.source(LocalFixtureSpec(path="fixtures/debug/raw_items.json"))
    builder.stage(SanitizeNode())
    builder.sink(NullSink())

    pipeline = builder.build()
    assert pipeline._snapshot_filter is None  # type: ignore[attr-defined]
