from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.application.scheduler_journal import (
    ensure_scheduler_slot,
    load_scheduler_journal,
    update_scheduler_slot,
)


class _Store:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}

    async def get_run_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.state[key] = value


@pytest.mark.asyncio
async def test_full_journal_keeps_newly_completed_slot() -> None:
    store = _Store()
    start = datetime(2026, 8, 2, tzinfo=UTC)
    ids = [(start + timedelta(hours=4 * i)).isoformat() for i in range(128)]
    store.state["bot_scheduler:journal"] = json.dumps(
        [{"slot_id": key, "run_state": "succeeded", "publish_state": "succeeded"} for key in ids]
    )
    now = datetime(2026, 11, 25, 21, 35, tzinfo=UTC)
    store.state["bot_scheduler:next_due_at"] = now.isoformat()
    slot = await ensure_scheduler_slot(store, now=now, interval_seconds=43200)
    assert slot is not None
    await update_scheduler_slot(
        store, str(slot["slot_id"]), run_state="succeeded", publish_state="succeeded"
    )
    assert [s["slot_id"] for s in await load_scheduler_journal(store)] == [
        *ids[1:],
        now.isoformat(),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_key", ["bot_scheduler:journal", "bot_scheduler:next_due_at"])
async def test_each_failed_write_leaves_the_scheduled_run_recoverable(failed_key: str) -> None:
    class FailOnce(_Store):
        async def set_run_state(self, key: str, value: str) -> None:
            if key == failed_key:
                raise OSError("injected interrupted write")
            await super().set_run_state(key, value)

    now = datetime(2026, 11, 25, 21, 35, tzinfo=UTC)
    store = FailOnce()
    store.state["bot_scheduler:next_due_at"] = now.isoformat()
    with pytest.raises(OSError):
        await ensure_scheduler_slot(store, now=now, interval_seconds=43200)
    recovered = _Store()
    recovered.state = store.state.copy()
    slot = await ensure_scheduler_slot(recovered, now=now, interval_seconds=43200)
    assert slot is not None and slot["slot_id"] == now.isoformat()
    await update_scheduler_slot(
        recovered, now.isoformat(), run_state="succeeded", publish_state="succeeded"
    )
    assert await ensure_scheduler_slot(recovered, now=now, interval_seconds=43200) is None
    assert recovered.state["bot_scheduler:next_due_at"] == (now + timedelta(hours=12)).isoformat()
    assert len(await load_scheduler_journal(recovered)) == 1


@pytest.mark.asyncio
async def test_due_slot_is_fixed_and_next_due_is_advanced() -> None:
    store = _Store()
    now = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    last_attempt = now - timedelta(hours=4, minutes=5)

    slot = await ensure_scheduler_slot(
        store,
        now=now,
        interval_seconds=4 * 60 * 60,
        legacy_last_attempt=last_attempt,
    )

    assert slot is not None
    assert slot["scheduled_for"] == (last_attempt + timedelta(hours=4)).isoformat()
    assert (
        store.state["bot_scheduler:next_due_at"] == (last_attempt + timedelta(hours=8)).isoformat()
    )

    await update_scheduler_slot(
        store,
        str(slot["slot_id"]),
        run_state="succeeded",
        publish_state="succeeded",
    )
    assert (
        await ensure_scheduler_slot(
            store,
            now=now,
            interval_seconds=4 * 60 * 60,
            legacy_last_attempt=last_attempt,
        )
        is None
    )


@pytest.mark.asyncio
async def test_outage_coalesces_old_windows_and_skipped_delivery_is_terminal() -> None:
    store = _Store()
    start = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    store.state["bot_scheduler:next_due_at"] = start.isoformat()
    now = start + timedelta(hours=49)
    slot = await ensure_scheduler_slot(store, now=now, interval_seconds=43200)
    assert slot["scheduled_for"] == (start + timedelta(hours=48)).isoformat()
    await update_scheduler_slot(
        store, str(slot["slot_id"]), run_state="skipped", publish_state="skipped"
    )
    assert await ensure_scheduler_slot(store, now=now, interval_seconds=43200) is None


@pytest.mark.asyncio
async def test_incomplete_slot_wins_over_a_new_schedule_slot() -> None:
    store = _Store()
    now = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
    marker = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)

    first = await ensure_scheduler_slot(
        store,
        now=now,
        interval_seconds=4 * 60 * 60,
        recovery_marker=marker,
        publish_pending=True,
    )
    second = await ensure_scheduler_slot(
        store,
        now=now + timedelta(hours=4),
        interval_seconds=4 * 60 * 60,
        recovery_marker=marker,
        publish_pending=True,
    )

    assert first is not None
    assert second is not None
    assert second["slot_id"] == first["slot_id"]
    assert len(await load_scheduler_journal(store)) == 1


@pytest.mark.asyncio
async def test_recovery_slot_is_complete_only_after_both_phases() -> None:
    store = _Store()
    marker = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)

    slot = await ensure_scheduler_slot(
        store,
        now=marker + timedelta(hours=4),
        interval_seconds=4 * 60 * 60,
        recovery_marker=marker,
        publish_pending=True,
    )
    assert slot is not None
    assert slot["run_state"] == "succeeded"
    assert slot["publish_state"] == "pending"

    await update_scheduler_slot(
        store,
        str(slot["slot_id"]),
        publish_state="succeeded",
    )
    assert await load_scheduler_journal(store) == [
        {
            "slot_id": marker.isoformat(),
            "scheduled_for": marker.isoformat(),
            "run_state": "succeeded",
            "publish_state": "succeeded",
            "publish_since": marker.isoformat(),
            "run_attempts": 0,
        }
    ]


@pytest.mark.asyncio
async def test_preflight_skipped_slot_does_not_stay_incomplete() -> None:
    store = _Store()
    marker = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    slot = await ensure_scheduler_slot(
        store,
        now=marker,
        interval_seconds=4 * 60 * 60,
        recovery_marker=marker,
    )
    assert slot is not None

    await update_scheduler_slot(
        store,
        str(slot["slot_id"]),
        run_state="skipped",
        publish_state="succeeded",
        publish_reason="llm_preflight_blocked",
    )
    assert await load_scheduler_journal(store) == [
        {
            "slot_id": marker.isoformat(),
            "scheduled_for": marker.isoformat(),
            "run_state": "skipped",
            "publish_state": "succeeded",
            "publish_since": marker.isoformat(),
            "run_attempts": 0,
            "publish_reason": "llm_preflight_blocked",
        }
    ]


@pytest.mark.asyncio
async def test_terminal_publish_skip_slot_does_not_stay_incomplete() -> None:
    store = _Store()
    marker = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    slot = await ensure_scheduler_slot(
        store,
        now=marker,
        interval_seconds=4 * 60 * 60,
        recovery_marker=marker,
    )
    assert slot is not None

    await update_scheduler_slot(
        store,
        str(slot["slot_id"]),
        run_state="succeeded",
        publish_state="skipped",
        publish_reason="terminal_rejections:card_validation_rejected:14",
    )

    assert await load_scheduler_journal(store) == [
        {
            "slot_id": marker.isoformat(),
            "scheduled_for": marker.isoformat(),
            "run_state": "succeeded",
            "publish_state": "skipped",
            "publish_since": marker.isoformat(),
            "run_attempts": 0,
            "publish_reason": "terminal_rejections:card_validation_rejected:14",
        }
    ]
