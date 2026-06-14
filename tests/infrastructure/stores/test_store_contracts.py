"""Parametrized Store contract tests.

Both InMemoryStore and SQLiteStore must exhibit identical observable behaviour.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from job_ftch.domain import DedupKeyKind, RememberedDedupKey, SourceKind
from job_ftch.infrastructure.stores.in_memory import InMemoryStore

try:
    from job_ftch.infrastructure.stores.sqlite import SQLiteStore as _SQLiteStore

    _SQLITE_AVAILABLE = True
except ImportError:
    _SQLITE_AVAILABLE = False


def _make_memory() -> InMemoryStore:
    return InMemoryStore()


def _make_sqlite() -> object:
    assert _SQLITE_AVAILABLE, "aiosqlite not installed"
    return _SQLiteStore(":memory:")  # type: ignore[no-untyped-call]


STORE_FACTORIES: list[pytest.param] = [  # type: ignore[type-arg]
    pytest.param(_make_memory, id="memory"),
    pytest.param(
        _make_sqlite,
        id="sqlite",
        marks=pytest.mark.skipif(not _SQLITE_AVAILABLE, reason="aiosqlite not installed"),
    ),
]


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_mark_processed_idempotent(factory: Callable[[], object]) -> None:
    """mark_processed is idempotent and has_processed reflects it."""
    store = factory()
    assert not await store.has_processed("x")  # type: ignore[union-attr]
    await store.mark_processed("x")  # type: ignore[union-attr]
    assert await store.has_processed("x")  # type: ignore[union-attr]
    await store.mark_processed("x")  # second call must not raise
    assert await store.has_processed("x")  # type: ignore[union-attr]


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_run_state_namespacing(factory: Callable[[], object]) -> None:
    """set_run_state/get_run_state correctly namespace by source_kind+source_name."""
    store = factory()
    await store.set_run_state("cursor", "A", source_kind="tg", source_name="ch1")  # type: ignore[union-attr]
    await store.set_run_state("cursor", "B", source_kind="tg", source_name="ch2")  # type: ignore[union-attr]
    assert await store.get_run_state("cursor", source_kind="tg", source_name="ch1") == "A"  # type: ignore[union-attr]
    assert await store.get_run_state("cursor", source_kind="tg", source_name="ch2") == "B"  # type: ignore[union-attr]
    assert (
        await store.get_run_state("cursor") is None
    )  # bare key → None  # type: ignore[union-attr]


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_dedup_key_round_trip(factory: Callable[[], object]) -> None:
    """remember_dedup_key → has_dedup_key → list_dedup_keys filtered by kind."""
    store = factory()
    record = RememberedDedupKey(
        key="url:https://example.com/1",
        kind=DedupKeyKind.URL,
        item_id="item-1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="example",
        url="https://example.com/1",
    )
    assert not await store.has_dedup_key(record.key)  # type: ignore[union-attr]
    await store.remember_dedup_key(record)  # type: ignore[union-attr]
    assert await store.has_dedup_key(record.key)  # type: ignore[union-attr]

    all_keys = await store.list_dedup_keys()  # type: ignore[union-attr]
    assert record in all_keys

    url_keys = await store.list_dedup_keys(DedupKeyKind.URL.value)  # type: ignore[union-attr]
    assert record in url_keys

    content_keys = await store.list_dedup_keys(DedupKeyKind.CONTENT.value)  # type: ignore[union-attr]
    assert record not in content_keys


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_source_strategy_round_trip(factory: Callable[[], object]) -> None:
    """save_source_strategy → get_source_strategy; None for unknown domain."""
    store = factory()
    assert await store.get_source_strategy("example.com") is None  # type: ignore[union-attr]
    await store.save_source_strategy("example.com", "playwright", "noop")  # type: ignore[union-attr]
    result = await store.get_source_strategy("example.com")  # type: ignore[union-attr]
    assert result == {"monitor": "playwright", "bypass": "noop"}
    assert await store.get_source_strategy("other.com") is None  # type: ignore[union-attr]
