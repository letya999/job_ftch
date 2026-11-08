from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, cast

from job_ftch.application.builder import PipelineBuilder
from job_ftch.application.pipeline import Pipeline
from job_ftch.config import get_settings
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter
from job_ftch.infrastructure.stores.sqlite import SQLiteStore
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.null_sink import NullSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _EmptySource:
    def fetch(self) -> AsyncIterator[RawItem]:
        async def items() -> AsyncIterator[RawItem]:
            if False:
                yield cast("Any", None)

        return items()


def test_pipeline_builder_clone_preserves_settings_snapshot() -> None:
    settings = get_settings().model_copy(update={"pipeline_decision_version": "snapshot-v1"})
    builder = PipelineBuilder(settings=settings)
    builder.store(InMemoryStore())
    builder.with_runtime_source(_EmptySource())
    builder.stage(SanitizeNode())
    builder.sink(NullSink())

    cloned = builder.clone()
    pipeline = cloned.build()

    assert pipeline._settings is settings  # type: ignore[attr-defined]


def test_execution_and_storage_methods_do_not_read_process_global_settings() -> None:
    for method in (Pipeline.run, SQLStoreAdapter.has_processed, SQLStoreAdapter.record_observation):
        assert "get_settings(" not in inspect.getsource(method)


async def test_pipeline_run_uses_settings_snapshot(monkeypatch: Any) -> None:
    settings = get_settings().model_copy(update={"pipeline_decision_version": "snapshot-v1"})
    builder = PipelineBuilder(settings=settings)
    builder.store(InMemoryStore())
    builder.with_runtime_source(_EmptySource())
    builder.stage(SanitizeNode())
    builder.sink(NullSink())
    pipeline = builder.build()

    def unexpected_global_settings() -> Any:
        raise AssertionError("Pipeline.run must not read global settings")

    monkeypatch.setattr("job_ftch.application.pipeline.get_settings", unexpected_global_settings)
    await pipeline.run()
    assert pipeline._decision_version == "snapshot-v1"  # type: ignore[attr-defined]


async def test_two_store_instances_keep_independent_retention_policy(tmp_path: Any) -> None:
    path = str(tmp_path / "retention.sqlite3")
    expired = SQLiteStore(path, processed_item_ttl_hours=0)
    persistent = SQLiteStore(path, processed_item_ttl_hours=None)
    try:
        await expired.mark_processed("same-item")
        assert not await expired.has_processed("same-item")
        assert await persistent.has_processed("same-item")
    finally:
        await expired.close()
        await persistent.close()


async def test_pipeline_observation_path_converges_on_same_identity(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "pipeline.sqlite3"))
    pipeline = Pipeline(
        source=_EmptySource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=NullSink(),
        store=store,
    )
    raw = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="same-item",
        text="same content",
    )
    try:
        await asyncio.gather(pipeline._record_observation(raw), pipeline._record_observation(raw))
        rows = await store.list_observations()
        assert len(rows) == 1
        assert rows[0].decision_version == pipeline._decision_version
    finally:
        await store.close()
