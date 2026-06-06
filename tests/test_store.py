from __future__ import annotations

import pytest

from infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.asyncio
async def test_in_memory_store_processed_ids_are_idempotent() -> None:
    store = InMemoryStore()

    assert await store.has_processed("item-1") is False
    assert await store.try_mark_processed("item-1") is True
    assert await store.try_mark_processed("item-1") is False
    await store.mark_processed("item-1")

    assert await store.has_processed("item-1") is True


@pytest.mark.asyncio
async def test_in_memory_store_dedup_keys_and_run_state_are_idempotent() -> None:
    store = InMemoryStore()

    assert await store.has_dedup_key("dedup-1") is False
    assert await store.try_remember_dedup_key("dedup-1", kind="raw", item_id="item-1")
    assert not await store.try_remember_dedup_key("dedup-1", kind="raw", item_id="item-2")
    await store.remember_dedup_key("dedup-1")
    await store.set_run_state("cursor", "abc")
    await store.set_run_state("cursor", "abc")
    await store.set_source_cursor("source:fixture", "42")
    await store.save_run_summary("run-1", {"run_id": "run-1"})
    await store.save_rejection(
        "rej-1",
        run_id="run-1",
        stage="source",
        reason="invalid_raw_item",
        payload={"reason": "invalid_raw_item"},
    )

    assert await store.has_dedup_key("dedup-1") is True
    assert await store.get_run_state("cursor") == "abc"
    assert await store.get_source_cursor("source:fixture") == "42"
