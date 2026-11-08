import asyncio

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
async def test_recovery_can_rely_on_destination_idempotency_after_mark_failure() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="ok",
        observation_id="o",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="sink",
        idempotency_key="b" * 64,
    )
    await store.enqueue_outbox(record)

    class _FlakyMarkStore:
        def __init__(self) -> None:
            self.failed_once = False

        async def list_pending_outbox(self, limit: int = 100) -> tuple[OutboxRecord, ...]:
            return await store.list_pending_outbox(limit)

        async def mark_outbox_delivered(self, idempotency_key: str) -> OutboxRecord | None:
            if not self.failed_once:
                self.failed_once = True
                raise OSError("state write failed after external success")
            return await store.mark_outbox_delivered(idempotency_key)

    effects: set[str] = set()
    attempts = 0

    async def deliver(delivery: OutboxRecord) -> None:
        nonlocal attempts
        attempts += 1
        effects.add(delivery.idempotency_key)

    flaky = _FlakyMarkStore()
    with pytest.raises(OSError, match="state write failed"):
        await recover_pending_outbox(flaky, deliver)

    assert attempts == 1
    assert effects == {record.idempotency_key}
    assert await recover_pending_outbox(flaky, deliver) == 1
    assert attempts == 2
    assert effects == {record.idempotency_key}
    assert await store.list_pending_outbox() == ()


@pytest.mark.anyio
async def test_two_recovery_workers_claim_one_pending_record() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="only-one",
        observation_id="o",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="sink",
        idempotency_key="b" * 64,
    )
    await store.enqueue_outbox(record)
    entered = asyncio.Event()
    finish = asyncio.Event()
    effects = 0

    async def deliver(_record: OutboxRecord) -> None:
        nonlocal effects
        effects += 1
        entered.set()
        await finish.wait()

    async def worker(owner: str) -> int:
        async def claim(row: OutboxRecord) -> bool:
            return await store.acquire_dedup_claim(
                f"outbox_delivery:{row.idempotency_key}", owner, ttl_seconds=300
            )

        async def release(row: OutboxRecord) -> None:
            await store.release_dedup_claim(f"outbox_delivery:{row.idempotency_key}", owner)

        return await recover_pending_outbox(store, deliver, claim=claim, release=release)

    first = asyncio.create_task(worker("first"))
    await entered.wait()
    assert await worker("second") == 0
    finish.set()
    assert await first == 1
    assert effects == 1


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
