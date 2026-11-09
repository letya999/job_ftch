from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from job_ftch.application.channel_publisher import DeliveryReceipt, RejectedDelivery, publish_jobs
from job_ftch.application.publication_backlog import (
    handled_key,
    mark_publication_handled,
    pending_candidate_ids,
    record_publish_candidate,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.asyncio
async def test_confirmed_delivery_settles_only_the_selected_target(make_job_record) -> None:
    store = InMemoryStore()
    job = make_job_record(job_id="old-job", fetched_at=datetime(2020, 1, 1, tzinfo=UTC))
    assert await record_publish_candidate(store, job)
    assert not await record_publish_candidate(store, job)
    assert await pending_candidate_ids(store, "@first") == {"old-job"}
    calls = []

    class Sender:
        async def send(self, target, delivered):
            calls.append(delivered.job_id)
            return DeliveryReceipt(target, 1, datetime.now(UTC))

    outcome = await publish_jobs(
        [job],
        target="@first",
        sender=Sender(),
        store=store,
        send_limit=10,
        throttle_seconds=0,
    )
    assert outcome.sent == 1 and calls == ["old-job"]
    assert not await pending_candidate_ids(store, "@first")
    assert await pending_candidate_ids(store, "@second") == {"old-job"}
    repeated = await publish_jobs(
        [job],
        target="@first",
        sender=Sender(),
        store=store,
        send_limit=10,
        throttle_seconds=0,
    )
    assert repeated.sent == 0 and calls == ["old-job"]


@pytest.mark.asyncio
async def test_unknown_delivery_keeps_candidate_pending(make_job_record) -> None:
    store = InMemoryStore()
    job = make_job_record(job_id="unknown-job")
    await record_publish_candidate(store, job)

    class Sender:
        async def send(self, *_):
            return SimpleNamespace()

    result = await publish_jobs(
        [job],
        target="@first",
        sender=Sender(),
        store=store,
        send_limit=10,
        throttle_seconds=0,
    )
    assert result.error == "delivery_requires_reconciliation"
    assert await pending_candidate_ids(store, "@first") == {"unknown-job"}


@pytest.mark.asyncio
async def test_terminal_rejection_retains_a_durable_reason(make_job_record) -> None:
    store = InMemoryStore()
    job = make_job_record(job_id="invalid-card")
    await record_publish_candidate(store, job)

    class Sender:
        async def send(self, *_):
            return RejectedDelivery("invalid_card")

    result = await publish_jobs([job], target="@first", sender=Sender(), store=store, send_limit=1)
    assert result.sent == 0 and result.terminal_skips == 1
    assert not await pending_candidate_ids(store, "@first")
    recorded = await store.get_run_state(f"{handled_key('@first')}:outcome:invalid-card")
    assert recorded is not None
    assert json.loads(recorded)["reason"] == "rejected:invalid_card"


@pytest.mark.asyncio
async def test_recovery_loads_old_candidate_outside_current_window(
    tmp_path, make_job_record, monkeypatch
) -> None:
    from job_ftch.adapters.telegram_bot.main import _recover_pending_scheduler_publish
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.config import Settings
    from job_ftch.infrastructure.backends.jobs.sqlite import SQLiteJobBackend

    store = InMemoryStore()
    backend = SQLiteJobBackend(Settings(job_store_path=tmp_path / "jobs.sqlite"))
    runtime = SimpleNamespace(
        store=store,
        job_backend=backend,
        settings=SimpleNamespace(bot_send_limit_per_run=10, bot_channel_throttle_seconds=0),
    )
    runner = TenantRunner({"tenant": runtime})
    job = make_job_record(
        job_id="before-hold",
        fetched_at=datetime(2020, 1, 1, tzinfo=UTC),
        post_type="job_posting",
        routing_decision="accept",
        canonical_url="https://example.org/jobs/old",
    )
    await backend.save(job)
    await record_publish_candidate(store, job)
    await store.set_run_state("bot_scheduler:pending_publish_since", datetime.now(UTC).isoformat())
    monkeypatch.setattr(runner, "latest_jobs", AsyncMock(return_value=[]))
    sent = []

    class Sender:
        async def send(self, target, delivered):
            sent.append(delivered.job_id)
            return DeliveryReceipt(target, 42, datetime.now(UTC))

    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.main.TelegramCardSender", lambda *a, **kw: Sender()
    )
    try:
        assert await _recover_pending_scheduler_publish(
            runner,
            SimpleNamespace(),
            tenant_id="tenant",
            publish_channel="@first",
            publish_user_id="owner",
        )
        assert sent == ["before-hold"]
        assert not await pending_candidate_ids(store, "@first")
        assert await store.get_run_state("bot_scheduler:pending_publish_since") == ""
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["handled", "missing", "uncertain"])
async def test_empty_recovery_closes_only_verified_settled_work(
    tmp_path, make_job_record, monkeypatch, state
) -> None:
    from job_ftch.adapters.telegram_bot.main import _recover_pending_scheduler_publish
    from job_ftch.application.scheduler_journal import ensure_scheduler_slot, load_scheduler_journal
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.config import Settings
    from job_ftch.domain.outbox import OutboxRecord, OutboxState
    from job_ftch.infrastructure.backends.jobs.sqlite import SQLiteJobBackend

    store = InMemoryStore()
    backend = SQLiteJobBackend(Settings(job_store_path=tmp_path / "jobs.sqlite"))
    runner = TenantRunner(
        {"tenant": SimpleNamespace(store=store, job_backend=backend, settings=SimpleNamespace())}
    )
    job = make_job_record(job_id="settled-or-missing", routing_decision="accept")
    await record_publish_candidate(store, job)
    if state != "missing":
        await backend.save(job)
        await mark_publication_handled(store, "@first", job, reason="already_published")
    if state == "uncertain":
        await store.enqueue_outbox(
            OutboxRecord(
                outbox_id="unknown-legacy-send",
                observation_id="old-input",
                content_hash="a" * 64,
                idempotency_key="b" * 64,
                sink_name="telegram:legacy",
                decision_version="publication-v1",
                state=OutboxState.UNCERTAIN,
            )
        )
    marker = datetime.now(UTC).isoformat()
    await store.set_run_state("bot_scheduler:pending_publish_since", marker)
    slot = await ensure_scheduler_slot(
        store,
        now=datetime.now(UTC),
        interval_seconds=3600,
        recovery_marker=datetime.now(UTC),
        publish_pending=True,
    )
    assert slot is not None
    monkeypatch.setattr(runner, "latest_jobs", AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(
        "job_ftch.adapters.telegram_bot.main.TelegramCardSender",
        lambda *a, **kw: SimpleNamespace(send=send),
    )
    try:
        result = await _recover_pending_scheduler_publish(
            runner,
            SimpleNamespace(),
            tenant_id="tenant",
            publish_channel="@first",
            publish_user_id="owner",
            slot_id=str(slot["slot_id"]),
        )
        send.assert_not_awaited()
        assert result is (state == "handled")
        assert await store.get_run_state("bot_scheduler:pending_publish_since") == (
            "" if state == "handled" else marker
        )
        journal = await load_scheduler_journal(store)
        assert journal[0]["publish_state"] == ("skipped" if state == "handled" else "failed")
        if state == "missing":
            assert await store.get_run_state("bot_publish:missing_candidates") == "1"
        if state == "uncertain":
            assert len(await store.list_uncertain_outbox()) == 1
    finally:
        await backend.close()
