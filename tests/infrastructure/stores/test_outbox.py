import os
import uuid

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


@pytest.mark.anyio
async def test_uncertain_delivery_is_not_replayed_as_pending() -> None:
    uncertain = getattr(OutboxState, "UNCERTAIN", None)
    assert uncertain is not None
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="uncertain",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        state=uncertain,
    )
    stored = await store.enqueue_outbox(record)
    assert stored.state is uncertain
    assert await store.list_pending_outbox() == ()


@pytest.mark.anyio
async def test_sqlite_persists_uncertain_delivery_without_replay(tmp_path) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "delivery.db")
    record = OutboxRecord(
        outbox_id="uncertain",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        state=OutboxState.UNCERTAIN,
    )
    try:
        assert (await store.enqueue_outbox(record)).state is OutboxState.UNCERTAIN
        assert await store.list_pending_outbox() == ()
    finally:
        await store.close()


@pytest.mark.anyio
async def test_only_owner_can_release_definitely_unsent_attempt() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="attempt",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    await store.enqueue_outbox(record)
    release = getattr(store, "release_uncertain_outbox", None)
    assert callable(release)
    await release(record.model_copy(update={"delivery_payload": {"attempt_id": "other"}}))
    assert (await store.enqueue_outbox(record)).delivery_payload["attempt_id"] == "owner"
    await release(record)
    replacement = record.model_copy(update={"delivery_payload": {"attempt_id": "new"}})
    assert (await store.enqueue_outbox(replacement)).delivery_payload["attempt_id"] == "new"


@pytest.mark.anyio
async def test_sqlite_release_checks_attempt_owner(tmp_path) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "release.db")
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    try:
        await store.enqueue_outbox(record)
        release = getattr(store, "release_uncertain_outbox", None)
        assert callable(release)
        await release(record.model_copy(update={"delivery_payload": {"attempt_id": "other"}}))
        assert (await store.enqueue_outbox(record)).delivery_payload["attempt_id"] == "owner"
        await release(record)
        replacement = record.model_copy(update={"delivery_payload": {"attempt_id": "new"}})
        assert (await store.enqueue_outbox(replacement)).delivery_payload["attempt_id"] == "new"
    finally:
        await store.close()


def test_postgres_release_checks_attempt_owner() -> None:
    from job_ftch.infrastructure.stores.postgres import PostgreSQLStore

    query = getattr(PostgreSQLStore, "_SQL_OUTBOX_RELEASE_UNCERTAIN", "")
    assert "payload_json" in query
    assert "state" in query


@pytest.mark.anyio
async def test_tenant_release_is_scoped_to_original_attempt() -> None:
    from job_ftch.application.tenant_store import TenantStore

    backing = InMemoryStore()
    first, second = TenantStore("first", backing), TenantStore("second", backing)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    first_stored = await first.enqueue_outbox(record)
    second_stored = await second.enqueue_outbox(record)
    release = getattr(first, "release_uncertain_outbox", None)
    assert callable(release)
    await release(first_stored)
    new_record = record.model_copy(update={"delivery_payload": {"attempt_id": "new"}})
    assert (await first.enqueue_outbox(new_record)).delivery_payload["attempt_id"] == "new"
    assert (await second.enqueue_outbox(new_record)) == second_stored


@pytest.mark.anyio
async def test_tenant_cannot_release_another_tenants_attempt() -> None:
    from job_ftch.application.tenant_store import TenantStore

    backing = InMemoryStore()
    first, second = TenantStore("first", backing), TenantStore("second", backing)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    other_tenant_record = await second.enqueue_outbox(record)
    with pytest.raises(ValueError):
        await first.release_uncertain_outbox(other_tenant_record)
    assert await second.enqueue_outbox(record) == other_tenant_record


@pytest.mark.anyio
async def test_tenant_confirms_only_own_uncertain_attempt() -> None:
    from job_ftch.application.tenant_store import TenantStore

    raw = InMemoryStore()
    first, second = TenantStore("one", raw), TenantStore("two", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    own, other = await first.enqueue_outbox(record), await second.enqueue_outbox(record)
    confirm = getattr(first, "confirm_uncertain_outbox", None)
    assert callable(confirm)
    with pytest.raises(ValueError):
        await confirm(other, {"message_id": 43})
    confirmed = await confirm(own, {"message_id": 42})
    assert confirmed is not None
    assert confirmed.state is OutboxState.DELIVERED
    assert await second.list_uncertain_outbox() == (other,)


@pytest.mark.anyio
async def test_operator_lists_only_their_uncertain_deliveries() -> None:
    from job_ftch.application.tenant_store import TenantStore

    backing = InMemoryStore()
    store = TenantStore("one", backing)
    other = TenantStore("two", backing)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"job_id": "job"},
        state=OutboxState.UNCERTAIN,
    )
    expected = await store.enqueue_outbox(record)
    await other.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    get_uncertain = getattr(store, "list_uncertain_outbox", None)
    assert callable(get_uncertain)
    assert await get_uncertain(limit=1) == (expected,)
    assert len(await get_uncertain()) == 2


@pytest.mark.anyio
async def test_tenant_attempt_lookup_excludes_other_tenants() -> None:
    from job_ftch.application.tenant_store import TenantStore

    raw = InMemoryStore()
    first, second = TenantStore("one", raw), TenantStore("two", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    expected = await first.enqueue_outbox(record)
    await second.enqueue_outbox(record)
    finder = getattr(first, "list_outbox_attempt", None)
    assert callable(finder)
    assert await finder("owner", "telegram:channel") == (expected,)


@pytest.mark.anyio
async def test_confirm_uncertain_attempt_stores_receipt() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    await store.enqueue_outbox(record)
    confirm = getattr(store, "confirm_uncertain_outbox", None)
    assert callable(confirm)
    confirmed = await confirm(record, {"chat_id": "channel", "message_id": 42})
    assert confirmed.state is OutboxState.DELIVERED
    assert confirmed.delivery_payload["receipt"]["message_id"] == 42


@pytest.mark.anyio
async def test_other_attempt_cannot_confirm_uncertain_delivery() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    await store.enqueue_outbox(record)
    other = record.model_copy(update={"delivery_payload": {"attempt_id": "other"}})
    result = await store.confirm_uncertain_outbox(other, {"message_id": 42})
    assert result is None
    assert await store.list_uncertain_outbox() == (record,)


@pytest.mark.anyio
async def test_find_attempt_includes_confirmed_and_uncertain_aliases() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    await store.enqueue_outbox(record)
    other = record.model_copy(update={"idempotency_key": "d" * 64, "outbox_id": "other"})
    await store.enqueue_outbox(other)
    await store.enqueue_outbox(
        record.model_copy(update={"idempotency_key": "e" * 64, "tenant_id": "another"})
    )
    await store.enqueue_outbox(
        record.model_copy(update={"idempotency_key": "f" * 64, "sink_name": "other"})
    )
    await store.enqueue_outbox(
        record.model_copy(
            update={"idempotency_key": "1" * 64, "delivery_payload": {"attempt_id": "other"}}
        )
    )
    await store.confirm_uncertain_outbox(record, {"message_id": 42})
    finder = getattr(store, "list_outbox_attempt", None)
    assert callable(finder)
    found = await finder("owner", "telegram:channel", tenant_id="default")
    assert {item.state for item in found} == {OutboxState.DELIVERED, OutboxState.UNCERTAIN}
    assert len(found) == 2


@pytest.mark.anyio
async def test_sqlite_finds_attempt_across_delivery_states(tmp_path) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "attempt.db")
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    try:
        await store.enqueue_outbox(record)
        other = record.model_copy(update={"idempotency_key": "d" * 64, "outbox_id": "other"})
        await store.enqueue_outbox(other)
        await store.confirm_uncertain_outbox(record, {"message_id": 42})
        finder = getattr(store, "list_outbox_attempt", None)
        assert callable(finder)
        found = await finder("owner", "telegram:channel", tenant_id="default")
        assert {item.state for item in found} == {OutboxState.DELIVERED, OutboxState.UNCERTAIN}
    finally:
        await store.close()


@pytest.mark.anyio
async def test_confirmed_receipt_cannot_be_overwritten() -> None:
    store = InMemoryStore()
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    await store.enqueue_outbox(record)
    confirmed = await store.confirm_uncertain_outbox(record, {"message_id": 42})
    assert confirmed is not None
    assert await store.confirm_uncertain_outbox(confirmed, {"message_id": 43}) is None


@pytest.mark.anyio
async def test_sqlite_confirms_only_original_attempt_with_receipt(tmp_path) -> None:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "confirm.db")
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    try:
        await store.enqueue_outbox(record)
        confirm = getattr(store, "confirm_uncertain_outbox", None)
        assert callable(confirm)
        other = record.model_copy(update={"delivery_payload": {"attempt_id": "other"}})
        assert await confirm(other, {"message_id": 43}) is None
        result = await confirm(record, {"message_id": 42})
        assert result is not None
        assert result.state is OutboxState.DELIVERED
        assert result.delivery_payload["receipt"]["message_id"] == 42
    finally:
        await store.close()


@pytest.mark.anyio
async def test_sqlite_lists_uncertain_records_for_tenant(tmp_path) -> None:
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore

    raw = SQLiteStore(tmp_path / "uncertain.db")
    first, second = TenantStore("one", raw), TenantStore("two", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"job_id": "job"},
        state=OutboxState.UNCERTAIN,
    )
    try:
        expected = await first.enqueue_outbox(record)
        await second.enqueue_outbox(record)
        assert callable(getattr(raw, "list_uncertain_outbox", None))
        assert await first.list_uncertain_outbox() == (expected,)
    finally:
        await raw.close()


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.getenv("JOB_FTCH_TEST_POSTGRES_DSN"), reason="isolated PostgreSQL unavailable"
)
async def test_postgres_uncertain_attempt_is_persistent_and_owner_scoped() -> None:
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.infrastructure.stores.postgres import PostgreSQLStore

    raw = PostgreSQLStore(os.environ["JOB_FTCH_TEST_POSTGRES_DSN"])
    store = TenantStore(f"test-{uuid.uuid4().hex}", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner"},
        state=OutboxState.UNCERTAIN,
    )
    try:
        stored = await store.enqueue_outbox(record)
        assert stored.state is OutboxState.UNCERTAIN
        assert await store.list_pending_outbox() == ()
        await store.release_uncertain_outbox(
            stored.model_copy(update={"delivery_payload": {"attempt_id": "other"}})
        )
        assert (await store.enqueue_outbox(record)).delivery_payload["attempt_id"] == "owner"
        await store.release_uncertain_outbox(stored)
        replacement = record.model_copy(update={"delivery_payload": {"attempt_id": "new"}})
        replacement_stored = await store.enqueue_outbox(replacement)
        assert replacement_stored.delivery_payload["attempt_id"] == "new"
        assert isinstance(getattr(raw, "_SQL_OUTBOX_CONFIRM_UNCERTAIN", None), str)
        confirmed = await raw.confirm_uncertain_outbox(replacement_stored, {"message_id": 42})
        assert confirmed is not None
        assert confirmed.state is OutboxState.DELIVERED
        assert confirmed.delivery_payload["receipt"]["message_id"] == 42
        alias = record.model_copy(
            update={"idempotency_key": "d" * 64, "delivery_payload": {"attempt_id": "new"}}
        )
        await store.enqueue_outbox(alias)
        assert isinstance(getattr(raw, "_SQL_OUTBOX_ATTEMPT", None), str)
        found = await raw.list_outbox_attempt("new", "telegram", tenant_id=stored.tenant_id)
        assert {item.state for item in found} == {OutboxState.DELIVERED, OutboxState.UNCERTAIN}
    finally:
        await raw.close()
