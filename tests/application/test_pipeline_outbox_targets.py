from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from job_ftch.application.delivery import SinkDeliveryTarget
from job_ftch.application.pipeline import Pipeline
from job_ftch.domain import JobRecord, OutboxRecord, OutboxState, RawItem, SourceKind
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


class _RecordingSink:
    def __init__(self) -> None:
        self.items: list[JobRecord] = []

    async def emit(self, item: JobRecord) -> None:
        self.items.append(item)


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
    await store.mark_outbox_delivered(initial[0][0])
    repeated = await pipeline._enqueue_outbox(raw, job)

    assert repeated[0][2] is OutboxState.DELIVERED
    await pipeline._emit_outbox_targets(job, repeated)
    assert target_sink.items == []
