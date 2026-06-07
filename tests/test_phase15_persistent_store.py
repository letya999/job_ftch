"""Regression tests for Phase 15 - Persistent Store."""

import pytest

from application.contracts import StoreConnector
from domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RememberedDedupKey,
    SourceKind,
)
from infrastructure.stores.in_memory import InMemoryStore
from infrastructure.stores.sqlite import SQLiteStore


@pytest.mark.asyncio
async def test_sqlite_store_idempotency():
    store = SQLiteStore(":memory:")
    # First run: mark items as processed
    assert not await store.has_processed("item-1")
    await store.mark_processed("item-1")
    # Second run: same item already processed
    assert await store.has_processed("item-1")
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_dedup_keys():
    store = SQLiteStore(":memory:")
    record = RememberedDedupKey(
        key="key1",
        kind=DedupKeyKind.FINGERPRINT,
        item_id="item1",
        source_kind=SourceKind.DEBUG,
        source_name="ch1",
    )
    await store.remember_dedup_key(record)
    keys = await store.list_dedup_keys()
    assert len(keys) == 1
    assert keys[0].key == "key1"

    # Test filtering
    keys_none = await store.list_dedup_keys(kind="none-existing")
    assert len(keys_none) == 0

    keys_kind = await store.list_dedup_keys(kind=DedupKeyKind.FINGERPRINT.value)
    assert len(keys_kind) == 1
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_run_state_namespacing():
    store = SQLiteStore(":memory:")
    await store.set_run_state("cursor", "v1", source_kind="telegram", source_name="ch1")
    await store.set_run_state("cursor", "v2", source_kind="telegram", source_name="ch2")
    assert await store.get_run_state("cursor", source_kind="telegram", source_name="ch1") == "v1"
    assert await store.get_run_state("cursor", source_kind="telegram", source_name="ch2") == "v2"
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_ping():
    store = SQLiteStore(":memory:")
    assert await store.ping() is True
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_connector_interface():
    """Verifies StoreConnector is independently testable with SQLite."""
    store = SQLiteStore(":memory:")
    await store.set("foo", "bar")
    assert await store.get("foo") == "bar"
    await store.set_add("myset", "member1")
    assert await store.set_contains("myset", "member1") is True
    assert await store.set_members("myset") == frozenset({"member1"})
    await store.delete("foo")
    assert await store.get("foo") is None
    await store.close()


@pytest.mark.asyncio
async def test_store_connector_protocol_isinstance():
    """InMemoryStore satisfies StoreConnector protocol."""
    store = InMemoryStore()
    assert isinstance(store, StoreConnector)


@pytest.mark.asyncio
async def test_sqlite_store_duplicate_records():
    store = SQLiteStore(":memory:")
    record = DuplicateRecord(
        item_id="item2",
        source_kind=SourceKind.DEBUG,
        source_name="ch1",
        reason=DuplicateRejectionReason.DUPLICATE_URL,
        duplicate_key="key2",
        matched_key="key1",
        matched_item_id="item1",
        matched_source_kind=SourceKind.DEBUG,
        matched_source_name="ch1",
        details="Duplicate of item1",
    )
    await store.record_duplicate(record)
    dups = await store.list_duplicate_records()
    assert len(dups) == 1
    assert dups[0].item_id == "item2"
    await store.close()
