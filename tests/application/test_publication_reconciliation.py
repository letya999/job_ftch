from __future__ import annotations

import importlib

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain import OutboxRecord, OutboxState
from job_ftch.infrastructure.stores.in_memory import InMemoryStore

pytestmark = pytest.mark.anyio


async def test_uncertain_publication_listing_is_tenant_scoped() -> None:
    try:
        module = importlib.import_module("job_ftch.application.publication_reconciliation")
    except ImportError:
        module = None
    listing = getattr(module, "list_uncertain_publications", None)
    assert callable(listing)
    raw = InMemoryStore()
    first, second = TenantStore("one", raw), TenantStore("two", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    expected = await first.enqueue_outbox(record)
    await second.enqueue_outbox(record)
    await first.enqueue_outbox(
        record.model_copy(update={"idempotency_key": "d" * 64, "sink_name": "other"})
    )
    rows = await listing(first)
    assert rows == [{"id": expected.idempotency_key, "job_id": "job", "target": "@channel"}]


async def test_listing_groups_identity_aliases_of_one_attempt() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    assert await module.list_uncertain_publications(store) == [
        {"id": first.idempotency_key, "job_id": "job", "target": "@channel"}
    ]


@pytest.mark.parametrize("delivered", [False, True])
async def test_listing_does_not_hide_the_101st_uncertain_attempt(delivered: bool) -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    for index in range(101):
        record = OutboxRecord(
            outbox_id=str(index),
            observation_id="job",
            content_hash="a" * 64,
            decision_version="v1",
            sink_name="telegram:channel",
            idempotency_key=f"{index:064x}",
            delivery_payload={"attempt_id": str(index), "job_id": "job", "target": "@channel"},
            state=OutboxState.UNCERTAIN,
        )
        await store.enqueue_outbox(record)
    rows = await module.list_uncertain_publications(store)
    assert len(rows) == 101
    error = None
    try:
        if delivered:
            await module.mark_verified_delivered(
                store, rows[-1]["id"], chat_id="-100", message_id=42
            )
        else:
            await module.mark_verified_unsent(store, rows[-1]["id"])
    except ValueError as exc:
        error = exc
    assert error is None
    assert len(await module.list_uncertain_publications(store)) == 100


async def test_verified_unsent_attempt_can_be_released_without_affecting_other_tenant() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    resolve = getattr(module, "mark_verified_unsent", None)
    assert callable(resolve)
    raw = InMemoryStore()
    first, second = TenantStore("one", raw), TenantStore("two", raw)
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    own, other = await first.enqueue_outbox(record), await second.enqueue_outbox(record)
    await resolve(first, own.idempotency_key)
    assert await first.list_uncertain_outbox() == ()
    assert await second.list_uncertain_outbox() == (other,)


async def test_verified_unsent_releases_all_identity_aliases() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    other = await store.enqueue_outbox(
        record.model_copy(
            update={
                "idempotency_key": "e" * 64,
                "delivery_payload": {
                    "attempt_id": "other",
                    "job_id": "other",
                    "target": "@channel",
                },
            }
        )
    )
    await module.mark_verified_unsent(store, first.idempotency_key)
    assert await store.list_uncertain_outbox() == (other,)


async def test_sibling_outside_listing_page_is_also_reconciled() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")

    class PagedStore(TenantStore):
        async def list_uncertain_outbox(self, limit=100):
            return (await super().list_uncertain_outbox(limit))[:1]

    store = PagedStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await module.mark_verified_unsent(store, first.idempotency_key)
    assert await super(PagedStore, store).list_uncertain_outbox() == ()


async def test_confirm_sibling_outside_listing_page() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")

    class PagedStore(TenantStore):
        async def list_uncertain_outbox(self, limit=100):
            return (await super().list_uncertain_outbox(limit))[:1]

    store = PagedStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await module.mark_verified_delivered(
        store, first.idempotency_key, chat_id="-100", message_id=42
    )
    assert await super(PagedStore, store).list_uncertain_outbox() == ()


async def test_confirmed_alias_prevents_releasing_remaining_alias_as_unsent() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    second = await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await store.confirm_uncertain_outbox(first, {"message_id": 42})
    with pytest.raises(ValueError):
        await module.mark_verified_unsent(store, second.idempotency_key)
    assert await store.list_uncertain_outbox() == (second,)


@pytest.mark.parametrize("existing", [{"chat_id": "-100", "message_id": 42}, {}])
async def test_confirmed_alias_rejects_conflicting_receipt(existing) -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    second = await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await store.confirm_uncertain_outbox(first, existing)
    with pytest.raises(ValueError):
        await module.mark_verified_delivered(
            store, second.idempotency_key, chat_id="-100", message_id=43
        )
    assert await store.list_uncertain_outbox() == (second,)


async def test_confirmed_alias_allows_matching_receipt() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    second = await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await store.confirm_uncertain_outbox(first, {"chat_id": "-100", "message_id": 42})
    await module.mark_verified_delivered(
        store, second.idempotency_key, chat_id="-100", message_id=42
    )
    assert await store.list_uncertain_outbox() == ()


async def test_unknown_attempt_cannot_be_reconciled() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    error = None
    try:
        await module.mark_verified_unsent(store, "missing")
    except Exception as exc:
        error = exc
    assert isinstance(error, ValueError)


async def test_malformed_attempt_cannot_release_other_records() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    with pytest.raises(ValueError):
        await module.mark_verified_unsent(store, first.idempotency_key)
    assert len(await store.list_uncertain_outbox()) == 2


async def test_malformed_attempt_cannot_confirm_other_records() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    with pytest.raises(ValueError):
        await module.mark_verified_delivered(
            store, first.idempotency_key, chat_id="-100", message_id=42
        )
    assert len(await store.list_uncertain_outbox()) == 2


async def test_unknown_attempt_cannot_be_marked_delivered() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    error = None
    try:
        await module.mark_verified_delivered(store, "missing", chat_id="-100", message_id=42)
    except Exception as exc:
        error = exc
    assert isinstance(error, ValueError)


async def test_verified_delivery_saves_receipt_and_job_ledger() -> None:
    import json
    from hashlib import sha256

    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    resolve = getattr(module, "mark_verified_delivered", None)
    assert callable(resolve)
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    await resolve(store, saved.idempotency_key, chat_id="-100", message_id=42)
    assert await store.list_uncertain_outbox() == ()
    confirmed = await store.enqueue_outbox(record)
    assert confirmed.state is OutboxState.DELIVERED
    assert confirmed.delivery_payload["receipt"]["message_id"] == 42
    assert "job" in json.loads(await store.get_run_state("bot_publish:sent_ids"))
    target_key = sha256(b"@channel").hexdigest()
    assert "job" in json.loads(await store.get_run_state(f"bot_publish:target:{target_key}:ids"))


async def test_verified_delivery_confirms_all_identity_aliases() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    first = await store.enqueue_outbox(record)
    await store.enqueue_outbox(record.model_copy(update={"idempotency_key": "d" * 64}))
    await module.mark_verified_delivered(
        store, first.idempotency_key, chat_id="-100", message_id=42
    )
    assert await store.list_uncertain_outbox() == ()


@pytest.mark.parametrize(
    "chat_id,message_id", [("-100", 0), ("-100", -1), ("-100", True), ("", 42)]
)
async def test_invalid_receipt_does_not_resolve_or_mark_publication(chat_id, message_id) -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    with pytest.raises(ValueError):
        await module.mark_verified_delivered(
            store, saved.idempotency_key, chat_id=chat_id, message_id=message_id
        )
    assert await store.list_uncertain_outbox() == (saved,)
    assert await store.get_run_state("bot_publish:sent_ids") is None


async def test_verified_delivery_without_job_id_does_not_invent_one() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")
    store = TenantStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": None, "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    await module.mark_verified_delivered(
        store, saved.idempotency_key, chat_id="-100", message_id=42
    )
    assert await store.get_run_state("bot_publish:sent_ids") is None
    assert (await store.enqueue_outbox(record)).state is OutboxState.DELIVERED


async def test_stale_attempt_is_not_marked_published() -> None:
    module = importlib.import_module("job_ftch.application.publication_reconciliation")

    class ChangedStore(TenantStore):
        async def confirm_uncertain_outbox(self, record, receipt):
            return None

    store = ChangedStore("one", InMemoryStore())
    record = OutboxRecord(
        outbox_id="id",
        observation_id="job",
        content_hash="a" * 64,
        decision_version="v1",
        sink_name="telegram:channel",
        idempotency_key="c" * 64,
        delivery_payload={"attempt_id": "owner", "job_id": "job", "target": "@channel"},
        state=OutboxState.UNCERTAIN,
    )
    saved = await store.enqueue_outbox(record)
    with pytest.raises(ValueError):
        await module.mark_verified_delivered(
            store, saved.idempotency_key, chat_id="-100", message_id=42
        )
    assert await store.get_run_state("bot_publish:sent_ids") is None
