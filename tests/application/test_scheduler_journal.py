from __future__ import annotations

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
