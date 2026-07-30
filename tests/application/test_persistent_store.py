"""Regression tests for Phase 15 - Persistent Store."""

import tempfile
from pathlib import Path

import pytest

from job_ftch.application.contracts import StoreConnector
from job_ftch.application.source_assessment import load_source_assessment, store_source_assessment
from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    ObservationLedgerEntry,
    OutboxRecord,
    RawItem,
    RememberedDedupKey,
    SourceKind,
)
from job_ftch.domain.observation import content_hash_for_raw_item
from job_ftch.domain.outbox import delivery_idempotency_key
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
    SourceIngestState,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sqlite import SQLiteStore

# ---------------------------------------------------------------------------
# SQLiteStore — basic contracts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_store_idempotency():
    store = SQLiteStore(":memory:")
    assert not await store.has_processed("item-1")
    await store.mark_processed("item-1")
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
    """Verifies StoreConnector KV+set operations work end-to-end."""
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


# ---------------------------------------------------------------------------
# SQLiteStore — extended coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_store_persistence_across_instances():
    """Data written by one SQLiteStore instance survives in a second one on the same file."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store1 = SQLiteStore(db_path)
        await store1.mark_processed("item-persist")
        await store1.close()

        store2 = SQLiteStore(db_path)
        assert await store2.has_processed("item-persist")
        await store2.close()


@pytest.mark.asyncio
async def test_sqlite_store_full_dedup_key_equality():
    """list_dedup_keys returns full field equality, not just the key string."""
    store = SQLiteStore(":memory:")
    record = RememberedDedupKey(
        key="exact-key",
        kind=DedupKeyKind.URL,
        item_id="item-42",
        source_kind=SourceKind.DEBUG,
        source_name="chan",
    )
    await store.remember_dedup_key(record)
    returned = (await store.list_dedup_keys())[0]
    assert returned.key == record.key
    assert returned.kind == record.kind
    assert returned.item_id == record.item_id
    assert returned.source_kind == record.source_kind
    assert returned.source_name == record.source_name
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_get_run_state_missing_key():
    store = SQLiteStore(":memory:")
    result = await store.get_run_state("nonexistent", source_kind="tg", source_name="ch")
    assert result is None
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_isinstance_connector():
    """SQLiteStore satisfies the StoreConnector protocol."""
    store = SQLiteStore(":memory:")
    assert isinstance(store, StoreConnector)
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_multiple_duplicate_records():
    store = SQLiteStore(":memory:")
    for i in range(3):
        rec = DuplicateRecord(
            item_id=f"item-{i}",
            source_kind=SourceKind.DEBUG,
            source_name="ch",
            reason=DuplicateRejectionReason.DUPLICATE_URL,
            duplicate_key=f"dk-{i}",
            matched_key="mk",
            matched_item_id="origin",
            matched_source_kind=SourceKind.DEBUG,
            matched_source_name="ch",
            details=f"duplicate of origin, index {i}",
        )
        await store.record_duplicate(rec)
    dups = await store.list_duplicate_records()
    assert len(dups) == 3
    assert {d.item_id for d in dups} == {"item-0", "item-1", "item-2"}
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_source_assessment_round_trip() -> None:
    store = SQLiteStore(":memory:")
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
            rationale="sqlite",
        ),
    )
    await store.save_source_assessment("tenant-a", result)
    assert await store.get_source_assessment("tenant-a", result.source_id) == result
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_source_ingest_state_round_trip() -> None:
    store = SQLiteStore(":memory:")
    state = SourceIngestState(source_id="career_site:jobs")
    await store.save_source_ingest_state("tenant-a", state)
    assert await store.get_source_ingest_state("tenant-a", state.source_id) == state
    await store.close()


@pytest.mark.asyncio
async def test_source_assessment_service_round_trip_via_tenant_store_sqlite() -> None:
    store = SQLiteStore(":memory:")
    tenant_store = TenantStore("tenant-a", store)
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
            rationale="tenant-store-sqlite",
        ),
    )
    await store_source_assessment(tenant_store, result)
    assert await load_source_assessment(tenant_store, result.source_id) == result
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_store_dedup_kind_filter_isolation():
    """Keys stored under one DedupKeyKind are not visible when filtering by another."""
    store = SQLiteStore(":memory:")
    fp_record = RememberedDedupKey(
        key="fp-key",
        kind=DedupKeyKind.FINGERPRINT,
        item_id="item-fp",
        source_kind=SourceKind.DEBUG,
        source_name="ch",
    )
    url_record = RememberedDedupKey(
        key="url-key",
        kind=DedupKeyKind.URL,
        item_id="item-url",
        source_kind=SourceKind.DEBUG,
        source_name="ch",
    )
    await store.remember_dedup_key(fp_record)
    await store.remember_dedup_key(url_record)

    all_keys = await store.list_dedup_keys()
    assert len(all_keys) == 2

    fp_only = await store.list_dedup_keys(kind=DedupKeyKind.FINGERPRINT.value)
    assert len(fp_only) == 1 and fp_only[0].key == "fp-key"

    url_only = await store.list_dedup_keys(kind=DedupKeyKind.URL.value)
    assert len(url_only) == 1 and url_only[0].key == "url-key"
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_clear_run_artifacts_removes_processed_dedup_and_ledgers():
    store = SQLiteStore(":memory:")
    tenant = TenantStore("ai_jobs", store)
    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="careers",
        external_id="raw-1",
        text="Senior AI Engineer",
    )
    dedup = RememberedDedupKey(
        key="content:key-1",
        kind=DedupKeyKind.CONTENT,
        item_id=raw.stable_id,
        source_kind=SourceKind.CAREER_SITE,
        source_name="careers",
    )
    observation = ObservationLedgerEntry(
        observation_id="obs-1",
        tenant_id="ai_jobs",
        stable_id=raw.stable_id,
        content_hash=content_hash_for_raw_item(raw),
        decision_version="raw-v1",
        raw_item=raw,
    )

    await tenant.set_run_state("config:telegram_publish_entity", "@out")
    await tenant.set_run_state("candidate_profile:user:profile", "{}")
    await tenant.mark_processed(raw.stable_id)
    await tenant.remember_dedup_key(dedup)
    await tenant.record_observation(observation)
    await tenant.save_source_snapshot("source-1", {raw.stable_id: "hash-1"})
    await tenant.save_source_ingest_state("ai_jobs", SourceIngestState(source_id="source-1"))
    await store.enqueue_outbox(
        OutboxRecord(
            outbox_id="outbox-1",
            tenant_id="ai_jobs",
            observation_id=observation.observation_id,
            content_hash=observation.content_hash,
            decision_version=observation.decision_version,
            sink_name="telegram",
            idempotency_key=delivery_idempotency_key(
                content_hash=observation.content_hash,
                decision_version=observation.decision_version,
                sink_name="telegram",
            ),
            delivery_payload={"job_id": raw.stable_id},
        )
    )
    other_outbox = OutboxRecord(
        outbox_id="outbox-other",
        tenant_id="other_tenant",
        observation_id="obs-other",
        content_hash="a" * 64,
        decision_version="raw-v1",
        sink_name="telegram",
        idempotency_key=delivery_idempotency_key(
            content_hash="a" * 64,
            decision_version="raw-v1",
            sink_name="telegram",
        ),
        delivery_payload={"job_id": "other"},
    )
    await store.enqueue_outbox(other_outbox)

    counts = await tenant.clear_run_artifacts()

    assert counts["observations"] == 1
    assert counts["kv"] >= 1
    assert counts["source_ingest_states"] == 1
    assert counts["outbox"] == 1
    assert await tenant.has_processed(raw.stable_id) is False
    assert await tenant.has_dedup_key(dedup.key) is False
    assert await tenant.get_observation(raw.stable_id, observation.content_hash) is None
    assert await tenant.get_last_run_snapshot("ai_jobs", "source-1") == frozenset()
    assert await store.list_pending_outbox(tenant_id="ai_jobs") == ()
    other_pending = await store.list_pending_outbox(tenant_id="other_tenant")
    assert len(other_pending) == 1
    assert other_pending[0].outbox_id == other_outbox.outbox_id
    assert other_pending[0].tenant_id == other_outbox.tenant_id
    assert await tenant.get_run_state("config:telegram_publish_entity") == "@out"
    assert await tenant.get_run_state("candidate_profile:user:profile") == "{}"
    await store.close()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_connector_protocol_isinstance():
    """InMemoryStore satisfies StoreConnector protocol."""
    store = InMemoryStore()
    assert isinstance(store, StoreConnector)


# ---------------------------------------------------------------------------
# InMemoryStore — split-brain regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_store_no_split_brain():
    """Store and StoreConnector views on InMemoryStore see the same data."""
    store = InMemoryStore()
    await store.mark_processed("item-x")
    # StoreConnector view must agree with Store view
    assert await store.has_processed("item-x") is True
    assert "item-x" in await store.set_members("processed")


@pytest.mark.asyncio
async def test_in_memory_store_dedup_no_split_brain():
    """remember_dedup_key is visible through both list_dedup_keys and set_members."""
    store = InMemoryStore()
    record = RememberedDedupKey(
        key="k1",
        kind=DedupKeyKind.FINGERPRINT,
        item_id="i1",
        source_kind=SourceKind.DEBUG,
        source_name="ch",
    )
    await store.remember_dedup_key(record)
    assert await store.has_dedup_key("k1") is True
    assert "k1" in await store.set_members("dedup_keys")
    assert (await store.list_dedup_keys())[0].key == "k1"
