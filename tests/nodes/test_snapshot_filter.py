"""Tests for SnapshotFilterNode (ADR-031, run-based snapshot)."""

from __future__ import annotations

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import SourceKind
from job_ftch.domain.triage import TriageRejectionReason
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sqlite import SQLiteStore
from job_ftch.nodes.snapshot_filter import SnapshotFilterNode, _content_hash, _derive_source_id


async def _run(node: SnapshotFilterNode, item):
    """Helper: process one item, returning the result or the drop exception."""
    try:
        return await node.process(item)
    except RawItemDropped as exc:
        return exc


# ---------------------------------------------------------------------------
# Construction / lifecycle
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_run_id() -> None:
    store = InMemoryStore()
    with pytest.raises(ValueError, match="non-empty run_id"):
        SnapshotFilterNode(store, tenant_id="t1", run_id="")


# ---------------------------------------------------------------------------
# 1. New item passes through
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_item_passes(make_raw_item) -> None:
    store = InMemoryStore()
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    item = make_raw_item(external_id="abc123", source_name="career")
    result = await node.process(item)
    assert result is item
    assert node.dropped_last_run == 0


# ---------------------------------------------------------------------------
# 2. Item in last run snapshot is dropped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_item_in_last_run_dropped(make_raw_item) -> None:
    store = InMemoryStore()
    # First run: process and save.
    node_a = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    item = make_raw_item(external_id="dup-1", source_name="career")
    assert await node_a.process(item) is item
    await node_a.save_and_purge()

    # Second run: same item should be dropped.
    node_b = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    drop = await _run(node_b, item)
    assert isinstance(drop, RawItemDropped)
    assert drop.reason is TriageRejectionReason.ALREADY_SEEN
    assert node_b.dropped_last_run == 1


@pytest.mark.anyio
async def test_unchanged_item_remains_in_the_next_complete_snapshot(make_raw_item) -> None:
    store = InMemoryStore()
    item = make_raw_item(external_id="stable", source_name="career")

    first = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    await first.process(item)
    await first.save_and_purge()

    second = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    assert isinstance(await _run(second, item), RawItemDropped)
    await second.save_and_purge()

    third = SnapshotFilterNode(store, tenant_id="t1", run_id="run-3")
    assert isinstance(await _run(third, item), RawItemDropped)


@pytest.mark.anyio
async def test_complete_source_snapshot_is_saved_when_another_source_is_partial(
    make_raw_item,
) -> None:
    store = InMemoryStore()
    completed = make_raw_item(external_id="complete", source_name="complete")
    partial = make_raw_item(external_id="partial", source_name="partial")
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    await node.process(completed)
    await node.process(partial)
    await node.save_and_purge(completed_source_ids=frozenset({"debug:complete"}))

    next_node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    assert isinstance(await _run(next_node, completed), RawItemDropped)
    assert await next_node.process(partial) is partial


@pytest.mark.anyio
async def test_snapshot_read_failure_fails_closed_by_default(make_raw_item) -> None:
    store = InMemoryStore()

    async def boom(*args, **kwargs):
        del args, kwargs
        raise OSError("snapshot table unavailable")

    store.get_last_run_snapshot_hashes = boom  # type: ignore[method-assign]
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")

    with pytest.raises(OSError, match="snapshot table unavailable"):
        await node.process(make_raw_item(external_id="abc123", source_name="career"))


@pytest.mark.anyio
async def test_snapshot_read_failure_can_fail_open(make_raw_item) -> None:
    store = InMemoryStore()

    async def boom(*args, **kwargs):
        del args, kwargs
        raise OSError("snapshot table unavailable")

    store.get_last_run_snapshot_hashes = boom  # type: ignore[method-assign]
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1", fail_open=True)

    item = make_raw_item(external_id="abc123", source_name="career")
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_changed_content_with_same_locator_is_reprocessed(make_raw_item) -> None:
    store = InMemoryStore()
    original = make_raw_item(external_id="same", source_name="career", text="original")
    first = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    await first.process(original)
    await first.save_and_purge()
    changed = original.model_copy(update={"text": "changed"})
    second = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    assert await second.process(changed) is changed


@pytest.mark.anyio
async def test_incomplete_run_does_not_replace_last_complete_snapshot(make_raw_item) -> None:
    store = InMemoryStore()
    item = make_raw_item(external_id="complete", source_name="career")
    complete = SnapshotFilterNode(store, tenant_id="t1", run_id="complete")
    await complete.process(item)
    await complete.save_and_purge()

    partial = SnapshotFilterNode(store, tenant_id="t1", run_id="partial")
    await partial.process(make_raw_item(external_id="partial", source_name="career"))
    await partial.save_and_purge(source_run_complete=False)

    next_run = SnapshotFilterNode(store, tenant_id="t1", run_id="next")
    assert isinstance(await _run(next_run, item), RawItemDropped)


# ---------------------------------------------------------------------------
# 3. Item not in last run passes (even with prior history)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_item_not_in_last_run_passes(make_raw_item) -> None:
    store = InMemoryStore()
    # Run 1 saves id=other.
    n1 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    other = make_raw_item(external_id="other", source_name="career")
    await n1.process(other)
    await n1.save_and_purge()

    # Run 2 saves id=current.
    n2 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    current = make_raw_item(external_id="current", source_name="career")
    assert await n2.process(current) is current
    await n2.save_and_purge()

    # Run 3: only current is in the last run; other should pass again.
    n3 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-3")
    again = make_raw_item(external_id="other", source_name="career")
    assert await n3.process(again) is again


# ---------------------------------------------------------------------------
# 4. TTL purge drops the newest runs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ttl_purge_drops_new_runs(make_raw_item) -> None:
    store = InMemoryStore()
    # In-memory purge: keep last max(1, older_than_days) runs per (tenant, source).
    # ttl=2 keeps 2 most recent runs; 3rd run should purge the 1st.
    for run_id in ("run-1", "run-2", "run-3"):
        node = SnapshotFilterNode(store, tenant_id="t1", run_id=run_id, ttl_days=2)
        item = make_raw_item(external_id=f"x-{run_id}", source_name="career")
        await node.process(item)
        await node.save_and_purge()

    # After 3 runs with ttl=2, the state should retain only the last 2 runs.
    # The item from run-1 is no longer in the last-run snapshot.
    # Verify by checking that running again with a fresh node treats run-1's id as new.
    n_final = SnapshotFilterNode(store, tenant_id="t1", run_id="run-4")
    item_run1 = make_raw_item(external_id="x-run-1", source_name="career")
    assert await n_final.process(item_run1) is item_run1


# ---------------------------------------------------------------------------
# 5. source_id isolation: different sources don't share snapshots
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_source_isolation(make_raw_item) -> None:
    store = InMemoryStore()
    n1 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    tg = make_raw_item(external_id="msg-1", source_name="telegram_channel", text="tg")
    career = make_raw_item(external_id="job-1", source_name="avito_career", text="cv")
    await n1.process(tg)
    await n1.process(career)
    await n1.save_and_purge()

    # New run, telegram source: msg-1 is in its last run → drop.
    # career source: job-1 is in its last run → drop.
    n2 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    drop_tg = await _run(n2, tg)
    drop_career = await _run(n2, career)
    assert isinstance(drop_tg, RawItemDropped)
    assert isinstance(drop_career, RawItemDropped)

    # A new item with a different external_id but same telegram source should pass.
    new_tg = make_raw_item(external_id="msg-2", source_name="telegram_channel", text="new")
    assert await n2.process(new_tg) is new_tg


# ---------------------------------------------------------------------------
# 6. tenant_id isolation: snapshots are scoped per tenant
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tenant_isolation(make_raw_item) -> None:
    store = InMemoryStore()
    # Tenant t1 processes an item.
    n1 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    item = make_raw_item(external_id="shared", source_name="career")
    await n1.process(item)
    await n1.save_and_purge()

    # Tenant t2 with same item id should NOT be dropped.
    n2 = SnapshotFilterNode(store, tenant_id="t2", run_id="run-1")
    assert await n2.process(item) is item


# ---------------------------------------------------------------------------
# 7. Item without stable_id passes through
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_item_without_stable_id_passes(make_raw_item) -> None:
    store = InMemoryStore()
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    # No external_id → no stable_id at construction.
    item = make_raw_item(source_name="career")
    item_no_sid = item.model_copy(update={"external_id": None, "url": None})
    result = await node.process(item_no_sid)
    assert result is item_no_sid


# ---------------------------------------------------------------------------
# 8. set_run_id rebinds the id used by save_and_purge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_run_id_aligns_with_pipeline(make_raw_item) -> None:
    store = InMemoryStore()
    node = SnapshotFilterNode(store, tenant_id="t1", run_id="placeholder")
    item = make_raw_item(external_id="x", source_name="career")
    await node.process(item)

    # TenantRunner aligns the filter's run_id with the pipeline's.
    node.set_run_id("pipeline-uuid-1")
    await node.save_and_purge()

    # New run with a different id: item should be dropped (it was in the last run).
    n2 = SnapshotFilterNode(store, tenant_id="t1", run_id="pipeline-uuid-2")
    drop = await _run(n2, item)
    assert isinstance(drop, RawItemDropped)


# ---------------------------------------------------------------------------
# 9. InMemoryStore round-trip preserves snapshot data
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_in_memory_store_round_trip(make_raw_item) -> None:
    store = InMemoryStore()
    n1 = SnapshotFilterNode(store, tenant_id="t1", run_id="r1")
    items = [make_raw_item(external_id=f"id-{i}", source_name="career") for i in range(5)]
    for it in items:
        await n1.process(it)
    await n1.save_and_purge()

    n2 = SnapshotFilterNode(store, tenant_id="t1", run_id="r2")
    # All 5 should be in the last-run snapshot.
    assert n2.bound_sources == set()  # bound lazily on first process
    for it in items:
        drop = await _run(n2, it)
        assert isinstance(drop, RawItemDropped)
    assert n2.bound_sources == {"debug:career"}


# ---------------------------------------------------------------------------
# 10. SQLite backend end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sqlite_backend_end_to_end(make_raw_item) -> None:
    store = SQLiteStore(path=":memory:")
    n1 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-1")
    item = make_raw_item(external_id="sqlite-1", source_name="career")
    await n1.process(item)
    await n1.save_and_purge()

    n2 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-2")
    drop = await _run(n2, item)
    assert isinstance(drop, RawItemDropped)
    assert drop.reason is TriageRejectionReason.ALREADY_SEEN

    # New item passes
    new_item = make_raw_item(external_id="sqlite-2", source_name="career")
    assert await n2.process(new_item) is new_item
    await n2.save_and_purge()

    # Run 3 keeps the unchanged item because run-2 observed it and therefore
    # persisted a complete source snapshot rather than only its changed rows.
    n3 = SnapshotFilterNode(store, tenant_id="t1", run_id="run-3")
    assert isinstance(await _run(n3, item), RawItemDropped)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_derive_source_id_with_kind() -> None:
    from job_ftch.domain.models import RawItem

    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="alpha_jobs",
        text="x",
        external_id="1",
    )
    assert _derive_source_id(item) == "telegram_channel:alpha_jobs"


def test_derive_source_id_fallback() -> None:
    from job_ftch.domain.models import RawItem

    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="local",
        text="x",
        external_id="1",
    )
    assert _derive_source_id(item) == "debug:local"


def test_content_hash_stable() -> None:
    from job_ftch.domain.models import RawItem

    item = RawItem(source_kind=SourceKind.DEBUG, source_name="d", text="hello", external_id="1")
    h1 = _content_hash(item)
    h2 = _content_hash(item.model_copy())
    assert h1 == h2
    other = item.model_copy(update={"text": "world"})
    assert _content_hash(other) != h1
