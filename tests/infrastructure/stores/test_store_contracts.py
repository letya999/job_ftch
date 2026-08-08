"""Parametrized Store contract tests.

Both InMemoryStore and SQLiteStore must exhibit identical observable behaviour.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import (
    DedupKeyKind,
    ObservationLedgerEntry,
    RawItem,
    RememberedDedupKey,
    SourceKind,
    content_hash_for_raw_item,
)
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
    SourceIngestState,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

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
async def test_observation_ledger_versions_content_not_locator(
    factory: Callable[[], object],
) -> None:
    store = factory()

    def entry(text: str) -> ObservationLedgerEntry:
        raw = RawItem(
            source_kind=SourceKind.DEBUG, source_name="ledger", external_id="one", text=text
        )
        return ObservationLedgerEntry(
            observation_id=f"obs-{text}",
            stable_id=raw.stable_id,
            content_hash=content_hash_for_raw_item(raw),
            decision_version="policy-v1",
            raw_item=raw,
        )

    first, changed = entry("first"), entry("changed")
    saved_first = await store.record_observation(first)  # type: ignore[union-attr]
    assert await store.record_observation(first) == saved_first  # type: ignore[union-attr]
    saved_changed = await store.record_observation(changed)  # type: ignore[union-attr]
    assert saved_first.content_version == 1
    assert saved_changed.content_version == 2
    assert await store.get_observation(first.stable_id, first.content_hash) == saved_first  # type: ignore[union-attr]


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
    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
async def test_dedup_claim_has_one_owner_and_releases(factory: Callable[[], object]) -> None:
    store = factory()
    assert await store.acquire_dedup_claim("content:x", "one", ttl_seconds=60)  # type: ignore[union-attr]
    assert not await store.acquire_dedup_claim("content:x", "two", ttl_seconds=60)  # type: ignore[union-attr]
    await store.release_dedup_claim("content:x", "one")  # type: ignore[union-attr]
    assert await store.acquire_dedup_claim("content:x", "two", ttl_seconds=60)  # type: ignore[union-attr]


@pytest.mark.parametrize("factory", STORE_FACTORIES)
async def test_compare_and_reserve_is_all_or_nothing(factory: Callable[[], object]) -> None:
    store = factory()
    result = await store.compare_and_reserve(("a", "b"), "owner1", ttl_seconds=60)  # type: ignore[union-attr]
    assert result.acquired is True
    assert set(result.reserved_keys) == {"a", "b"}
    result2 = await store.compare_and_reserve(("b", "c"), "owner2", ttl_seconds=60)  # type: ignore[union-attr]
    assert result2.acquired is False
    assert result2.conflicting_key == "b"
    result3 = await store.compare_and_reserve(("d", "e"), "owner2", ttl_seconds=60)  # type: ignore[union-attr]
    assert result3.acquired is True


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
    close = getattr(store, "close", None)
    if callable(close):
        await close()


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
    assert await store.get_dedup_key(record.key) == record  # type: ignore[union-attr]
    assert await store.get_dedup_key("missing") is None  # type: ignore[union-attr]

    all_keys = await store.list_dedup_keys()  # type: ignore[union-attr]
    assert record in all_keys

    url_keys = await store.list_dedup_keys(DedupKeyKind.URL.value)  # type: ignore[union-attr]
    assert record in url_keys

    content_keys = await store.list_dedup_keys(DedupKeyKind.CONTENT.value)  # type: ignore[union-attr]
    assert record not in content_keys
    close = getattr(store, "close", None)
    if callable(close):
        await close()


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
    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_source_assessment_round_trip(factory: Callable[[], object]) -> None:
    store = factory()
    result = SourceAssessmentResult(
        source_id="career_site:jobs",
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="contract",
        ),
    )
    await store.save_source_assessment("tenant-a", result)  # type: ignore[union-attr]
    loaded = await store.get_source_assessment("tenant-a", result.source_id)  # type: ignore[union-attr]
    assert loaded == result
    assert await store.get_source_assessment("tenant-b", result.source_id) is None  # type: ignore[union-attr]
    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_source_ingest_state_round_trip(factory: Callable[[], object]) -> None:
    store = factory()
    state = SourceIngestState(source_id="career_site:jobs")
    await store.save_source_ingest_state("tenant-a", state)  # type: ignore[union-attr]
    loaded = await store.get_source_ingest_state("tenant-a", state.source_id)  # type: ignore[union-attr]
    assert loaded == state
    assert await store.get_source_ingest_state("tenant-b", state.source_id) is None  # type: ignore[union-attr]
    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_sql_snapshot_save_is_atomic(factory: Callable[[], object]) -> None:
    store = factory()

    from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter

    if isinstance(store, SQLStoreAdapter):
        # Good row followed by a bad row (None for a TEXT field)
        # Note: We pass None to trigger an IntegrityError (NOT NULL constraint) on the second row
        # to prove that SQLite/Postgres roll back the first row.
        rows = (
            ("stable-1", "hash-1", "{}"),
            (None, "hash-2", "{}"),  # type: ignore[misc]
        )

        with pytest.raises(sqlite3.IntegrityError):
            await store.save_snapshot_rows("tenant-a", "career:foo", "run-1", rows)

        # Verify nothing was saved
        snapshot = await store.get_last_run_snapshot("tenant-a", "career:foo")
        assert len(snapshot) == 0

    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_observation_version_concurrent_updates_retry_or_succeed(
    factory: Callable[[], object], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = factory()
    from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter

    if isinstance(store, SQLStoreAdapter):
        original_execute = store._execute
        call_count = [0]

        async def mock_execute(self: Any, sql: str, params: tuple[Any, ...]) -> None:
            if "INSERT INTO jf_observations" in sql and call_count[0] == 0:
                call_count[0] += 1
                raise OSError("simulated unique constraint failure")
            return await original_execute(sql, params)

        monkeypatch.setattr(store, "_execute", mock_execute.__get__(store))

        from job_ftch.domain import (
            ObservationLedgerEntry,
            RawItem,
            SourceKind,
            content_hash_for_raw_item,
        )

        raw = RawItem(
            source_kind=SourceKind.DEBUG,
            source_name="ledger",
            external_id="concurrent",
            text="data",
        )
        entry = ObservationLedgerEntry(
            observation_id="obs-concurrent",
            tenant_id="default",
            stable_id=raw.stable_id,
            content_hash=content_hash_for_raw_item(raw),
            decision_version="policy-v1",
            raw_item=raw,
        )

        # It should retry and succeed
        recorded = await store.record_observation(entry)
        assert recorded.content_version == 1

        # Check that it actually retried
        assert call_count[0] == 1

    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", STORE_FACTORIES)
@pytest.mark.integration
async def test_sql_outbox_duplicate_enqueue_after_delivered_returns_delivered(
    factory: Callable[[], object],
) -> None:
    from job_ftch.domain import OutboxRecord, OutboxState

    store = factory()

    # Only test stores that support outbox
    if hasattr(store, "enqueue_outbox"):
        record = OutboxRecord(
            outbox_id="test_ob_1",
            tenant_id="default",
            sink_name="my_target",
            idempotency_key="ik_" + "1" * 61,
            observation_id="obs_1",
            content_hash="hash_" + "1" * 59,
            decision_version="v1",
            delivery_payload={"hello": "world"},
            state=OutboxState.OUTBOXED,
        )

        # Enqueue once
        enqueued = await store.enqueue_outbox(record)  # type: ignore[union-attr]
        assert enqueued.state == OutboxState.OUTBOXED

        # Mark delivered
        delivered = await store.mark_outbox_delivered(record.idempotency_key)  # type: ignore[union-attr]
        assert delivered is not None
        assert delivered.state == OutboxState.DELIVERED

        # Enqueue duplicate. Store should return the DB state (DELIVERED)
        enqueued_dup = await store.enqueue_outbox(record)  # type: ignore[union-attr]
        assert enqueued_dup.state == OutboxState.DELIVERED

    close = getattr(store, "close", None)
    if callable(close):
        await close()
