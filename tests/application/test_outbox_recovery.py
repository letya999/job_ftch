import pytest

from job_ftch.application.outbox import recover_pending_outbox
from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import OutboxRecord, OutboxState
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.anyio
async def test_recovery_marks_only_successful_delivery() -> None:
    store = InMemoryStore()
    ok = OutboxRecord(
        outbox_id="ok",
        observation_id="o",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="sink",
        idempotency_key="b" * 64,
    )
    bad = ok.model_copy(update={"outbox_id": "bad", "idempotency_key": "c" * 64})
    await store.enqueue_outbox(ok)
    await store.enqueue_outbox(bad)

    async def deliver(record: OutboxRecord) -> None:
        if record.outbox_id == "bad":
            raise OSError("temporary")

    assert await recover_pending_outbox(store, deliver) == 1
    delivered = await store.mark_outbox_delivered(ok.idempotency_key)
    assert delivered is not None
    assert delivered.state is OutboxState.DELIVERED
    assert len(await store.list_pending_outbox()) == 1


@pytest.mark.anyio
async def test_tenant_outbox_records_are_isolated() -> None:
    store = InMemoryStore()
    first = TenantStore("first", store)
    second = TenantStore("second", store)
    record = OutboxRecord(
        outbox_id="outbox",
        observation_id="raw-1",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="target",
        idempotency_key="b" * 64,
    )

    first_record = await first.enqueue_outbox(record)
    second_record = await second.enqueue_outbox(record)

    assert first_record.idempotency_key != second_record.idempotency_key
    assert await first.list_pending_outbox() == (first_record,)
    assert await second.list_pending_outbox() == (second_record,)
