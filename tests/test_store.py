from __future__ import annotations

import pytest

from infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.asyncio
async def test_in_memory_store_processed_ids_are_idempotent() -> None:
    store = InMemoryStore()

    assert await store.has_processed("item-1") is False
    await store.mark_processed("item-1")
    await store.mark_processed("item-1")

    assert await store.has_processed("item-1") is True


@pytest.mark.asyncio
async def test_in_memory_store_dedup_keys_and_run_state_are_idempotent() -> None:
    store = InMemoryStore()

    assert await store.has_dedup_key("dedup-1") is False
    await store.remember_dedup_key("dedup-1")
    await store.remember_dedup_key("dedup-1")
    await store.set_run_state("cursor", "abc")
    await store.set_run_state("cursor", "abc")

    assert await store.has_dedup_key("dedup-1") is True
    assert await store.get_run_state("cursor") == "abc"
