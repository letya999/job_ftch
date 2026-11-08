from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from job_ftch.application.llm_continuation import (
    ObservationContinuation,
    observation_continuation,
)
from job_ftch.application.llm_dispatch import LLMRequestDeferredError
from job_ftch.application.pipeline import Pipeline
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import RawItem, SourceKind, processed_key_for_raw_item
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import SanitizeNode
from job_ftch.sinks.json_file import JsonFileSink

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_pending_inputs_survive_restart_without_refetch(tmp_path: Path, monkeypatch) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="saved", external_id=str(i), text="job")
        for i in range(3)
    ]
    fetches = 0
    calls: list[str] = []
    ready = False

    class Source:
        async def fetch(self):  # type: ignore[no-untyped-def]
            nonlocal fetches
            fetches += 1
            for item in items:
                yield item

    class Node:
        async def process(self, item: RawItem) -> RawItem:
            calls.append(item.stable_id)
            if not ready:
                raise LLMRequestDeferredError("queued", datetime.now(UTC) + timedelta(seconds=30))
            return item

    store = InMemoryStore()
    checkpoint = ObservationContinuation(store, "logical-run", "tenant")
    await checkpoint.load()
    token = observation_continuation.set(checkpoint)
    try:
        result = await Pipeline(
            source=Source(),
            sanitize_node=SanitizeNode(),
            nodes=[Node()],
            sink=JsonFileSink(tmp_path / "out.json"),
            store=store,
            tenant_id="tenant",
            pipeline_item_concurrency=1,
        ).run()
    finally:
        observation_continuation.reset(token)
    assert result.completion_state == "waiting_llm"
    assert result.failed == 0
    assert len(calls) == 1

    restored = ObservationContinuation(store, "logical-run", "tenant")
    await restored.load()
    entries = await restored.entries()
    assert {entry.stable_id for entry in entries} == {item.stable_id for item in items}
    for item in items:
        assert not await store.has_processed(processed_key_for_raw_item(item))

    class ResumeBuilder:
        def with_runtime_source(self, source):
            self.source = source

        async def run_async(self, **_):
            return await Pipeline(
                source=self.source,
                sanitize_node=SanitizeNode(),
                nodes=[Node()],
                sink=JsonFileSink(tmp_path / "out.json"),
                store=store,
                tenant_id="tenant",
                pipeline_item_concurrency=1,
            ).run()

    runner = TenantRunner({"tenant": SimpleNamespace(store=store)})
    monkeypatch.setattr(runner, "_build_runtime_catalog", AsyncMock(return_value=(None, [])))
    monkeypatch.setattr(
        runner, "_build_runtime_builder", AsyncMock(return_value=(ResumeBuilder(), None))
    )
    monkeypatch.setattr(runner, "_persist_terminal_summary", AsyncMock())
    fresh_sources = AsyncMock(side_effect=AssertionError("Continuation must not acquire sources"))
    monkeypatch.setattr(runner, "_ensure_runtime_sources_loaded", fresh_sources)

    ready = True
    token = observation_continuation.set(restored)
    try:
        result = await runner._run_tenant_bound(
            "tenant", run_id="after-restart", lock_already_held=True
        )
    finally:
        observation_continuation.reset(token)
    assert fetches == 1
    fresh_sources.assert_not_awaited()
    assert result.replayed_observations == 3
    assert result.emitted == 3
    assert not restored.references
    for item in items:
        assert await store.has_processed(processed_key_for_raw_item(item))


@pytest.mark.asyncio
async def test_missing_checkpoint_observation_is_an_error() -> None:
    store = InMemoryStore()
    await store.set_run_state("llm_continuation:run", '[["missing", "hash"]]')
    checkpoint = ObservationContinuation(store, "run", "tenant")
    await checkpoint.load()
    with pytest.raises(ValueError, match="missing from the ledger"):
        await checkpoint.entries()


@pytest.mark.asyncio
async def test_sink_failure_keeps_observation_for_restart() -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="saved", external_id="unsaved", text="vacancy"
    )
    store = InMemoryStore()
    checkpoint = ObservationContinuation(store, "failed-sink", "tenant")

    class Source:
        async def fetch(self):
            yield item

    class Sink:
        async def emit(self, _):
            raise OSError("storage unavailable")

    token = observation_continuation.set(checkpoint)
    try:
        summary = await Pipeline(
            source=Source(),
            sanitize_node=SanitizeNode(),
            nodes=[],
            sink=Sink(),
            store=store,
            tenant_id="tenant",
        ).run()
    finally:
        observation_continuation.reset(token)
    assert summary.failed > 0
    assert not await store.has_processed(processed_key_for_raw_item(item))
    restored = ObservationContinuation(store, "failed-sink", "tenant")
    await restored.load()
    assert [entry.raw_item for entry in await restored.entries()] == [item]
