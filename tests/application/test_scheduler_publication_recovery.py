import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("aiogram")

from job_ftch.adapters.telegram_bot import main as bot_main
from job_ftch.application.channel_publisher import DeliveryReceipt
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.publication_backlog import (
    mark_publication_handled,
    pending_candidate_ids,
    record_publish_candidate,
)
from job_ftch.application.scheduler_journal import load_scheduler_journal
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.fixture
def scheduler(monkeypatch):
    store = InMemoryStore()
    runner = MagicMock()
    runner.get_runtime.return_value = SimpleNamespace(
        store=store,
        settings=SimpleNamespace(bot_send_limit_per_run=10, bot_channel_throttle_seconds=0),
    )
    runner.tenant_ids.return_value = ["tenant"]
    runner.get_schedule_interval = AsyncMock(return_value=3600)
    runner.get_publish_channel = AsyncMock(return_value="@first")
    runner.get_publish_user_id = AsyncMock(return_value="owner")
    runner.has_candidate_profile_data = AsyncMock(return_value=True)
    runner.latest_jobs = AsyncMock(return_value=[])
    runner.pending_publish_jobs = AsyncMock(return_value=[])
    sent = []
    cycles = {"count": 0, "stop": 1}

    async def sleep(seconds):
        if seconds >= 60:
            cycles["count"] += 1
            if cycles["count"] >= cycles["stop"]:
                raise asyncio.CancelledError

    class Sender:
        async def send(self, target, job):
            sent.append(job.job_id)
            return DeliveryReceipt(target, 42, datetime.now(UTC))

    monkeypatch.setattr(bot_main.asyncio, "sleep", sleep)
    monkeypatch.setattr(bot_main, "TelegramCardSender", lambda *a, **kw: Sender())
    monkeypatch.setattr(bot_main, "_send_scheduler_run_report", AsyncMock())
    return store, runner, MagicMock(), sent, cycles


@pytest.mark.asyncio
async def test_empty_delivery_window_releases_waiting_ingest(scheduler, make_job_record):
    store, runner, bot, sent, cycles = scheduler
    old = make_job_record(
        job_id="published-before-replay", fetched_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    await record_publish_candidate(store, old)
    await mark_publication_handled(store, "@first", old, reason="already_published")
    await store.set_run_state("bot_scheduler:pending_publish_since", "2020-01-02T00:00:00+00:00")
    await store.set_run_state(
        "bot_scheduler:last_publish_error", "pending publish candidates are missing"
    )
    await store.set_run_state(
        "bot_scheduler:last_attempt_at", (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    )
    previous_success = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await store.set_run_state("bot_scheduler:last_success_at", previous_success)
    runner.run_tenant = AsyncMock(
        return_value=RunSummary(emitted=1, new_publish_candidates=0, completion_state="waiting_llm")
    )
    cycles["stop"] = 2

    with pytest.raises(asyncio.CancelledError):
        await bot_main._run_scheduler_loop(runner, bot)

    runner.run_tenant.assert_awaited_once_with("tenant", user_id="owner")
    assert not sent
    assert not await pending_candidate_ids(store, "@first")
    assert await store.get_run_state("bot_scheduler:pending_publish_since") == ""
    assert await store.get_run_state("bot_scheduler:last_publish_error") == ""
    assert await store.get_run_state("bot_scheduler:last_success_at") == previous_success
    journal = await load_scheduler_journal(store)
    assert any(row.get("publish_reason") == "no_pending_candidates" for row in journal)


@pytest.mark.asyncio
async def test_waiting_replay_does_not_queue_already_handled_emits(scheduler):
    store, runner, bot, sent, cycles = scheduler
    runner.run_tenant = AsyncMock(
        return_value=RunSummary(emitted=2, new_publish_candidates=0, completion_state="waiting_llm")
    )

    with pytest.raises(asyncio.CancelledError):
        await bot_main._run_scheduler_loop(runner, bot)

    runner.run_tenant.assert_awaited_once()
    runner.latest_jobs.assert_not_awaited()
    assert not sent
    assert not await store.get_run_state("bot_scheduler:pending_publish_since")


@pytest.mark.asyncio
@pytest.mark.parametrize("report_age_minutes,expected_calls", [(70, 1), (10, 0)])
async def test_waiting_replay_reports_again_after_schedule_interval(
    scheduler, report_age_minutes, expected_calls
):
    store, runner, bot, _, _ = scheduler
    await store.set_run_state("bot_scheduler:llm_notice", "waiting_llm")
    await store.set_run_state(
        "bot_scheduler:last_owner_report_success_at",
        (datetime.now(UTC) - timedelta(minutes=report_age_minutes)).isoformat(),
    )
    runner.run_tenant = AsyncMock(return_value=RunSummary(completion_state="waiting_llm"))

    with pytest.raises(asyncio.CancelledError):
        await bot_main._run_scheduler_loop(runner, bot)

    assert bot_main._send_scheduler_run_report.await_count == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["completed", "waiting_llm"])
async def test_replayed_accept_publishes_from_backlog_outside_attempt_window(
    scheduler, make_job_record, completion
):
    store, runner, bot, sent, cycles = scheduler
    cycles["stop"] = 2 if completion == "waiting_llm" else 1
    job = make_job_record(
        job_id="accepted-from-saved-input",
        fetched_at=datetime(2020, 1, 1, tzinfo=UTC),
        post_type="job_posting",
        routing_decision="accept",
        canonical_url="https://example.org/jobs/saved",
    )

    async def run(*args, **kwargs):
        await record_publish_candidate(store, job)
        return RunSummary(emitted=1, new_publish_candidates=1, completion_state=completion)

    async def pending(*args, **kwargs):
        return [job] if await pending_candidate_ids(store, "@first") else []

    runner.run_tenant = AsyncMock(side_effect=run)
    runner.pending_publish_jobs = AsyncMock(side_effect=pending)

    with pytest.raises(asyncio.CancelledError):
        await bot_main._run_scheduler_loop(runner, bot)

    assert sent == [job.job_id]
    assert not await pending_candidate_ids(store, "@first")
    assert await store.get_run_state("bot_scheduler:pending_publish_since") == ""
    assert await store.get_run_state("bot_scheduler:last_publish_sent") == "1"
