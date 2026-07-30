from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.infrastructure.stores.in_memory import InMemoryStore


class _FakeSettings:
    def __init__(self, ttl_hours: int | None) -> None:
        self.processed_item_ttl_hours = ttl_hours


@pytest.mark.asyncio
async def test_has_processed_respects_fresh_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryStore()
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: _FakeSettings(24),
        raising=False,
    )

    await store.mark_processed("item-1")

    assert await store.has_processed("item-1") is True


@pytest.mark.asyncio
async def test_has_processed_expires_stale_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryStore()
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: _FakeSettings(24),
        raising=False,
    )
    await store.set_add("processed", "item-1")
    stale = datetime.now(UTC) - timedelta(hours=25)
    await store.set("processed_at:item-1", stale.isoformat())

    assert await store.has_processed("item-1") is False


@pytest.mark.asyncio
async def test_has_processed_falls_back_to_legacy_membership_when_timestamp_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryStore()
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: _FakeSettings(24),
        raising=False,
    )
    await store.set_add("processed", "item-legacy")

    assert await store.has_processed("item-legacy") is True
