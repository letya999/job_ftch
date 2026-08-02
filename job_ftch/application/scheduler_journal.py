"""Durable state for scheduled ingest and channel-delivery slots."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable


class SchedulerJournalStore(Protocol):
    async def get_run_state(self, key: str) -> str | None: ...

    async def set_run_state(self, key: str, value: str) -> None: ...


_JOURNAL_KEY = "bot_scheduler:journal"
_NEXT_DUE_KEY = "bot_scheduler:next_due_at"
_JOURNAL_LIMIT = 128


async def _maybe_await(value: object) -> object:
    if hasattr(value, "__await__"):
        return await cast("Awaitable[Any]", value)
    return value


def parse_scheduler_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def load_scheduler_journal(store: SchedulerJournalStore) -> list[dict[str, object]]:
    raw = await _maybe_await(store.get_run_state(_JOURNAL_KEY))
    if not isinstance(raw, str) or not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and isinstance(item.get("slot_id"), str)]


async def _save_scheduler_journal(
    store: SchedulerJournalStore, journal: list[dict[str, object]]
) -> None:
    # Keep incomplete slots forever; trim only the oldest completed history.
    incomplete = [item for item in journal if not _is_complete(item)]
    completed = [item for item in journal if _is_complete(item)][-_JOURNAL_LIMIT:]
    await _maybe_await(store.set_run_state(_JOURNAL_KEY, json.dumps(incomplete + completed)))


def _is_complete(record: dict[str, object]) -> bool:
    return record.get("run_state") == "succeeded" and record.get("publish_state") == "succeeded"


def _find_slot(journal: list[dict[str, object]], slot_id: str) -> dict[str, object] | None:
    return next((item for item in journal if item.get("slot_id") == slot_id), None)


async def ensure_scheduler_slot(
    store: SchedulerJournalStore,
    *,
    now: datetime,
    interval_seconds: int,
    legacy_last_attempt: datetime | None = None,
    recovery_marker: datetime | None = None,
    publish_pending: bool = False,
) -> dict[str, object] | None:
    """Return the oldest incomplete slot or create the next due fixed slot."""
    if interval_seconds <= 0:
        return None
    journal = await load_scheduler_journal(store)
    incomplete = [item for item in journal if not _is_complete(item)]
    if incomplete:
        incomplete.sort(key=lambda item: str(item.get("slot_id", "")))
        return incomplete[0]

    if recovery_marker is not None:
        slot_id = recovery_marker.isoformat()
        record = {
            "slot_id": slot_id,
            "scheduled_for": slot_id,
            "run_state": "succeeded" if publish_pending else "pending",
            "publish_state": "pending",
            "publish_since": slot_id,
            "run_attempts": 0,
        }
        journal.append(record)
        await _save_scheduler_journal(store, journal)
        return record

    next_due = parse_scheduler_timestamp(
        await _maybe_await(store.get_run_state(_NEXT_DUE_KEY))
    )
    if next_due is None:
        baseline = legacy_last_attempt or now
        next_due = baseline + timedelta(seconds=interval_seconds) if legacy_last_attempt else baseline
        await _maybe_await(store.set_run_state(_NEXT_DUE_KEY, next_due.isoformat()))
    if now < next_due:
        return None

    slot_id = next_due.isoformat()
    record = {
        "slot_id": slot_id,
        "scheduled_for": slot_id,
        "run_state": "pending",
        "publish_state": "pending",
        "publish_since": None,
        "run_attempts": 0,
    }
    journal.append(record)
    next_due += timedelta(seconds=interval_seconds)
    await _maybe_await(store.set_run_state(_NEXT_DUE_KEY, next_due.isoformat()))
    await _save_scheduler_journal(store, journal)
    return record


async def update_scheduler_slot(
    store: SchedulerJournalStore,
    slot_id: str,
    **updates: object,
) -> dict[str, object] | None:
    journal = await load_scheduler_journal(store)
    record = _find_slot(journal, slot_id)
    if record is None:
        return None
    record.update(updates)
    await _save_scheduler_journal(store, journal)
    return record
