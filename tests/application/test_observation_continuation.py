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
    assert len(calls) == 3

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
    monkeypatch.setattr(runner, "get_schedule_interval", AsyncMock(return_value=3600))
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
async def test_replay_preserves_old_work_and_acquisition_resumes_on_schedule() -> None:
    from job_ftch.domain.observation import ObservationLedgerEntry, content_hash_for_raw_item

    store = InMemoryStore()
    checkpoint = ObservationContinuation(store, "recent-first", "tenant")
    now = datetime.now(UTC)
    for identity, observed_at in (
        ("old", now - timedelta(days=1)),
        ("new", now),
    ):
        item = RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="saved",
            external_id=identity,
            text="job",
        )
        entry = ObservationLedgerEntry(
            observation_id=identity,
            tenant_id="tenant",
            stable_id=item.stable_id,
            content_hash=content_hash_for_raw_item(item),
            decision_version="test",
            raw_item=item,
            observed_at=observed_at,
        )
        await store.record_observation(entry)
        await checkpoint.add(entry)
    assert [entry.observation_id for entry in await checkpoint.entries()] == ["old", "new"]
    assert not await checkpoint.acquisition_due(3600)
    await store.set_run_state(checkpoint.acquisition_key, (now - timedelta(hours=2)).isoformat())
    assert await checkpoint.acquisition_due(3600)
    before = set(checkpoint.references)
    await checkpoint.note_acquisition()
    restored = ObservationContinuation(store, "recent-first", "tenant")
    await restored.load()
    assert not await restored.acquisition_due(3600)
    assert restored.references == before


@pytest.mark.asyncio
async def test_pending_item_does_not_block_independent_terminal_items(tmp_path: Path) -> None:
    items = [
        RawItem(source_kind=SourceKind.DEBUG, source_name="saved", external_id=i, text=i)
        for i in ("waiting", "ready", "irrelevant")
    ]

    class Source:
        async def fetch(self):
            for item in items:
                yield item

    class Node:
        async def process(self, item: RawItem) -> RawItem | None:
            if item.external_id == "waiting":
                raise LLMRequestDeferredError("queued", datetime.now(UTC) + timedelta(seconds=30))
            return None if item.external_id == "irrelevant" else item

    store = InMemoryStore()
    checkpoint = ObservationContinuation(store, "independent", "tenant")
    token = observation_continuation.set(checkpoint)
    sink = SimpleNamespace(emit=AsyncMock())
    try:
        summary = await Pipeline(
            source=Source(),
            sanitize_node=SanitizeNode(),
            nodes=[Node()],
            sink=sink,
            store=store,
            tenant_id="tenant",
            pipeline_item_concurrency=1,
        ).run()
    finally:
        observation_continuation.reset(token)
    assert summary.completion_state == "waiting_llm"
    assert summary.emitted == 1
    assert summary.failed == 0
    assert sink.emit.await_args.args[0].external_id == "ready"
    assert {entry.raw_item.external_id for entry in await checkpoint.entries()} == {"waiting"}
    assert not await store.has_processed(processed_key_for_raw_item(items[0]))
    assert await store.has_processed(processed_key_for_raw_item(items[1]))


@pytest.mark.asyncio
async def test_due_acquisition_does_not_require_an_empty_checkpoint(monkeypatch) -> None:
    from job_ftch.domain.observation import ObservationLedgerEntry, content_hash_for_raw_item

    store = InMemoryStore()
    item = RawItem(source_kind=SourceKind.DEBUG, source_name="old", external_id="1", text="job")
    entry = ObservationLedgerEntry(
        observation_id="old",
        tenant_id="tenant",
        stable_id=item.stable_id,
        content_hash=content_hash_for_raw_item(item),
        decision_version="test",
        raw_item=item,
        observed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    await store.record_observation(entry)
    checkpoint = ObservationContinuation(store, "scheduled", "tenant")
    await checkpoint.add(entry)
    runner = TenantRunner({"tenant": SimpleNamespace(store=store)})
    monkeypatch.setattr(runner, "get_schedule_interval", AsyncMock(return_value=3600))
    acquire = AsyncMock(side_effect=OSError("acquisition reached"))
    monkeypatch.setattr(runner, "_ensure_runtime_sources_loaded", acquire)
    token = observation_continuation.set(checkpoint)
    try:
        with pytest.raises(OSError, match="acquisition reached"):
            await runner._run_tenant_bound("tenant", run_id="fresh", lock_already_held=True)
    finally:
        observation_continuation.reset(token)
    acquire.assert_awaited_once()
    assert checkpoint.references == {(entry.stable_id, entry.content_hash)}


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


@pytest.mark.asyncio
async def test_checkpoint_writes_only_when_references_change(monkeypatch) -> None:
    from job_ftch.domain.observation import ObservationLedgerEntry, content_hash_for_raw_item

    store = InMemoryStore()
    write = AsyncMock(wraps=store.set_run_state)
    monkeypatch.setattr(store, "set_run_state", write)
    item = RawItem(source_kind=SourceKind.DEBUG, source_name="saved", external_id="one", text="job")
    entry = ObservationLedgerEntry(
        observation_id="one",
        tenant_id="tenant",
        stable_id=item.stable_id,
        content_hash=content_hash_for_raw_item(item),
        decision_version="test",
        raw_item=item,
    )
    checkpoint = ObservationContinuation(store, "same-run", "tenant")
    await checkpoint.add(entry)
    restored = ObservationContinuation(store, "same-run", "tenant")
    await restored.load()
    await restored.add(entry)
    assert write.await_count == 1
    await restored.complete(entry.stable_id, entry.content_hash)
    await restored.complete(entry.stable_id, entry.content_hash)
    assert write.await_count == 2
    assert await store.get_run_state(restored.key) == "[]"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "complete"])
async def test_failed_checkpoint_write_can_be_retried(monkeypatch, operation) -> None:
    from job_ftch.domain.observation import ObservationLedgerEntry, content_hash_for_raw_item

    store = InMemoryStore()
    item = RawItem(source_kind=SourceKind.DEBUG, source_name="saved", external_id="one", text="job")
    entry = ObservationLedgerEntry(
        observation_id="one",
        tenant_id="tenant",
        stable_id=item.stable_id,
        content_hash=content_hash_for_raw_item(item),
        decision_version="test",
        raw_item=item,
    )
    checkpoint = ObservationContinuation(store, "retry-run", "tenant")
    if operation == "complete":
        await checkpoint.add(entry)
    before = set(checkpoint.references)
    persisted_before = await store.get_run_state(checkpoint.key)
    save = store.set_run_state
    write = AsyncMock(side_effect=OSError("storage unavailable"))
    monkeypatch.setattr(store, "set_run_state", write)

    async def change():
        if operation == "add":
            await checkpoint.add(entry)
        else:
            await checkpoint.complete(entry.stable_id, entry.content_hash)

    with pytest.raises(OSError, match="storage unavailable"):
        await change()
    assert checkpoint.references == before
    assert await store.get_run_state(checkpoint.key) == persisted_before
    write.side_effect = save
    await change()
    restored = ObservationContinuation(store, "retry-run", "tenant")
    await restored.load()
    expected = {(entry.stable_id, entry.content_hash)} if operation == "add" else set()
    assert checkpoint.references == restored.references == expected
    assert write.await_count == 2
