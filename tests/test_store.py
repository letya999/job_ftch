from __future__ import annotations

import pytest

from domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RememberedDedupKey,
    SourceKind,
)
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
    record = RememberedDedupKey(
        key="dedup-1",
        kind=DedupKeyKind.CONTENT,
        item_id="item-1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
    )

    assert await store.has_dedup_key("dedup-1") is False
    await store.remember_dedup_key(record)
    await store.remember_dedup_key(record)
    await store.set_run_state("cursor", "abc")
    await store.set_run_state("cursor", "abc")

    assert await store.has_dedup_key("dedup-1") is True
    assert await store.list_dedup_keys(DedupKeyKind.CONTENT.value) == (record,)
    assert await store.get_run_state("cursor") == "abc"


@pytest.mark.asyncio
async def test_in_memory_store_persists_duplicate_explainability() -> None:
    store = InMemoryStore()
    duplicate = DuplicateRecord(
        item_id="item-2",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ai-jobs",
        reason=DuplicateRejectionReason.DUPLICATE_NEAR_MATCH,
        duplicate_key="content:new",
        matched_key="content:old",
        matched_item_id="item-1",
        matched_source_kind=SourceKind.CAREER_SITE,
        matched_source_name="ClickHouse",
        score=96.4,
        details="near duplicate",
    )

    await store.record_duplicate(duplicate)

    assert await store.list_duplicate_records() == (duplicate,)


@pytest.mark.asyncio
async def test_in_memory_store_run_state_backward_compat_without_namespace():
    store = InMemoryStore()
    await store.set_run_state("cursor", "xyz")
    val = await store.get_run_state("cursor")
    assert val == "xyz"
    # With namespace, should NOT see the value set without namespace
    assert await store.get_run_state("cursor", source_kind="tg", source_name="ch") is None
