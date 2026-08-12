from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from job_ftch.application.delivery import SinkDeliveryTarget
from job_ftch.application.pipeline import Pipeline
from job_ftch.config import get_settings
from job_ftch.domain import (
    DeliveryEnvelope,
    JobRecord,
    OutboxRecord,
    OutboxState,
    RawItem,
    SourceKind,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.fanout import FanOutSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _EmptySource:
    def fetch(self) -> AsyncIterator[RawItem]:
        async def items() -> AsyncIterator[RawItem]:
            if False:
                yield cast("RawItem", None)

        return items()


class _OneItemSource:
    def __init__(self, item: RawItem) -> None:
        self._item = item

    def fetch(self) -> AsyncIterator[RawItem]:
        async def items() -> AsyncIterator[RawItem]:
            yield self._item

        return items()


class _JobStage:
    async def process(self, item: RawItem) -> JobRecord:
        del item
        return _job()


class _RecordingSink:
    def __init__(self) -> None:
        self.items: list[JobRecord] = []
        self.envelopes: list[DeliveryEnvelope] = []

    async def emit(self, item: JobRecord) -> None:
        self.items.append(item)

    async def emit_envelope(self, envelope: DeliveryEnvelope, item: JobRecord) -> None:
        self.envelopes.append(envelope)
        await self.emit(item)


class _FailingSink(_RecordingSink):
    async def emit(self, item: JobRecord) -> None:
        raise RuntimeError("sink is down")


def _job() -> JobRecord:
    return JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="Engineer",
        company="Acme",
        description="Role",
    )


@pytest.mark.asyncio
async def test_pending_outbox_replays_only_its_named_target() -> None:
    store = InMemoryStore()
    job = _job()
    record = OutboxRecord(
        outbox_id="outbox-1",
        observation_id="raw-1",
        content_hash="a" * 64,
        decision_version="pipeline-v1",
        sink_name="delivery:test",
        idempotency_key="b" * 64,
        delivery_payload=job.model_dump(mode="json"),
    )
    await store.enqueue_outbox(record)

    first = _RecordingSink()

    class _SecondSink(_RecordingSink):
        pass

    second = _SecondSink()
    pipeline = Pipeline(
        source=_EmptySource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=FanOutSink([first]),
        store=store,
        delivery_targets=[SinkDeliveryTarget("delivery:test", second)],
    )

    await pipeline.run()

    assert first.items == []
    assert second.items == [job]
    assert await store.list_pending_outbox() == ()


@pytest.mark.asyncio
async def test_delivered_outbox_record_is_not_delivered_again() -> None:
    store = InMemoryStore()
    job = _job()
    target_sink = _RecordingSink()
    pipeline = Pipeline(
        source=_EmptySource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=_RecordingSink(),
        store=store,
        delivery_targets=[SinkDeliveryTarget("delivery:test", target_sink)],
    )
    raw = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="raw-1",
        text="role",
    )

    initial = await pipeline._enqueue_outbox(raw, job)
    assert {record.sink_name for record in initial} == {"primary:sink", "delivery:test"}
    for record in initial:
        await store.mark_outbox_delivered(record.idempotency_key)
    repeated = await pipeline._enqueue_outbox(raw, job)

    assert {record.state for record in repeated} == {OutboxState.DELIVERED}
    await pipeline._emit_outbox_targets(job, repeated)
    assert target_sink.items == []


@pytest.mark.asyncio
async def test_partial_delivery_retry_does_not_repeat_primary_sink() -> None:
    store = InMemoryStore()
    job = _job()
    primary_sink = _RecordingSink()
    failing_target = _FailingSink()
    pipeline = Pipeline(
        source=_EmptySource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=primary_sink,
        store=store,
        delivery_targets=[SinkDeliveryTarget("delivery:test", failing_target)],
    )
    raw = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="raw-1",
        text="role",
    )

    first_records = await pipeline._enqueue_outbox(raw, job)
    with pytest.raises(RuntimeError, match="sink is down"):
        await pipeline._emit_outbox_targets(job, first_records)

    assert primary_sink.items == [job]
    assert [envelope.sink_name for envelope in primary_sink.envelopes] == ["primary:sink"]

    retry_records = await pipeline._enqueue_outbox(raw, job)
    assert {
        record.sink_name: record.state for record in retry_records
    } == {
        "primary:sink": OutboxState.DELIVERED,
        "delivery:test": OutboxState.OUTBOXED,
    }

    with pytest.raises(RuntimeError, match="sink is down"):
        await pipeline._emit_outbox_targets(job, retry_records)

    assert primary_sink.items == [job]


@pytest.mark.asyncio
async def test_outbox_identity_uses_effective_decision_version() -> None:
    store = InMemoryStore()
    job = _job()
    pipeline = Pipeline(
        source=_EmptySource(),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=_RecordingSink(),
        store=store,
    )
    raw = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="raw-1",
        text="role",
    )

    pipeline._decision_version = "policy-v1"
    v1_records = await pipeline._enqueue_outbox(raw, job)
    pipeline._decision_version = "policy-v2"
    v2_records = await pipeline._enqueue_outbox(raw, job)

    assert [record.decision_version for record in v1_records] == ["policy-v1"]
    assert [record.decision_version for record in v2_records] == ["policy-v2"]
    assert v1_records[0].idempotency_key != v2_records[0].idempotency_key


@pytest.mark.asyncio
async def test_pipeline_run_uses_injected_decision_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected_settings = get_settings().model_copy(
        update={"pipeline_decision_version": "injected-policy"}
    )
    monkeypatch.setattr(
        "job_ftch.application.pipeline.get_settings",
        lambda: get_settings().model_copy(update={"pipeline_decision_version": "global-policy"}),
    )
    store = InMemoryStore()
    sink = _RecordingSink()
    raw = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="test",
        external_id="raw-1",
        text="role",
    )
    pipeline = Pipeline(
        source=_OneItemSource(raw),
        sanitize_node=SanitizeNode(),
        nodes=[_JobStage()],
        sink=sink,
        store=store,
        settings=injected_settings,
    )

    await pipeline.run()

    assert [envelope.decision_version for envelope in sink.envelopes] == ["injected-policy"]
