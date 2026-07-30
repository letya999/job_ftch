import pytest

from job_ftch.domain import OutboxRecord, OutboxState
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.anyio
async def test_outbox_is_idempotent_and_deliverable() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="o1",
        observation_id="obs",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="sink",
        idempotency_key="b" * 64,
    )
    assert (await store.enqueue_outbox(record)).state is OutboxState.OUTBOXED
    assert (await store.enqueue_outbox(record)).outbox_id == "o1"
    assert (
        await store.mark_outbox_delivered(record.idempotency_key)
    ).state is OutboxState.DELIVERED
