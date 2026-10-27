from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_ftch.application.llm_quota import (
    LLMPreflightResult,
    check_llm_before_run,
)


class Store:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}

    async def get_run_state(self, key: str) -> str | None:
        return self.state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self.state[key] = value


class Provider:
    def __init__(self, *results: LLMPreflightResult) -> None:
        self.results = list(results)
        self.calls = 0

    async def preflight(self) -> LLMPreflightResult:
        self.calls += 1
        return self.results.pop(0)


@pytest.mark.anyio
async def test_quota_backoff_is_two_short_retries_then_schedule_pause() -> None:
    store = Store()
    provider = Provider(
        LLMPreflightResult(False, quota_exhausted=True),
        LLMPreflightResult(False, quota_exhausted=True),
        LLMPreflightResult(False, quota_exhausted=True),
    )
    start = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

    first = await check_llm_before_run(
        store, provider, now=start, normal_interval_seconds=43200, max_retries=2
    )
    second = await check_llm_before_run(
        store,
        provider,
        now=start + timedelta(minutes=30),
        normal_interval_seconds=43200,
        max_retries=2,
    )
    third = await check_llm_before_run(
        store,
        provider,
        now=start + timedelta(minutes=60),
        normal_interval_seconds=43200,
        max_retries=2,
    )

    assert first.status == "retry_scheduled"
    assert second.status == "retry_scheduled"
    assert third.status == "disabled_until_schedule"
    assert first.retry_at == start + timedelta(minutes=30)
    assert second.retry_at == start + timedelta(minutes=60)
    assert third.retry_at == start + timedelta(hours=12)
    assert provider.calls == 3


@pytest.mark.anyio
async def test_quota_pause_does_not_call_provider_before_retry_time() -> None:
    store = Store()
    provider = Provider(LLMPreflightResult(False, quota_exhausted=True))
    start = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

    scheduled = await check_llm_before_run(store, provider, now=start)
    waiting = await check_llm_before_run(store, provider, now=start + timedelta(minutes=1))

    assert scheduled.status == "retry_scheduled"
    assert waiting.status == "retry_wait"
    assert waiting.retry_at == start + timedelta(minutes=30)
    assert provider.calls == 1


@pytest.mark.anyio
async def test_success_clears_quota_pause() -> None:
    store = Store()
    provider = Provider(
        LLMPreflightResult(False, quota_exhausted=True),
        LLMPreflightResult(True),
    )
    start = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

    await check_llm_before_run(store, provider, now=start)
    recovered = await check_llm_before_run(store, provider, now=start + timedelta(minutes=30))

    assert recovered.allowed is True
    assert store.state["bot_scheduler:llm_quota_retry_count"] == "0"
    assert store.state["bot_scheduler:llm_preflight_status"] == "ready"


@pytest.mark.anyio
async def test_five_retries_survive_restart_and_reset_at_next_window() -> None:
    store = Store()
    provider = Provider(*[LLMPreflightResult(False, quota_exhausted=True) for _ in range(7)])
    start = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    for attempt in range(6):
        decision = await check_llm_before_run(
            store, provider, now=start + timedelta(minutes=30 * attempt)
        )
        assert decision.status == ("disabled_until_schedule" if attempt == 5 else "retry_scheduled")
        restarted_store = Store()
        restarted_store.state = dict(store.state)
        store = restarted_store
    assert provider.calls == 6
    assert decision.retry_at == start + timedelta(hours=12)
    for minute in (151, 180, 600, 719):
        waiting = await check_llm_before_run(store, provider, now=start + timedelta(minutes=minute))
        assert waiting.allowed is False
    assert provider.calls == 6
    new_window = await check_llm_before_run(store, provider, now=start + timedelta(hours=12))
    assert new_window.status == "retry_scheduled"
    assert new_window.retry_number == 1
    assert provider.calls == 7


@pytest.mark.anyio
async def test_nonquota_failure_waits_without_using_quota_retries() -> None:
    store = Store()
    provider = Provider(LLMPreflightResult(False, error="unauthorized"))
    start = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    first = await check_llm_before_run(store, provider, now=start)
    second = await check_llm_before_run(store, provider, now=start + timedelta(seconds=60))
    assert first.status == "unavailable_until_schedule"
    assert second.status == "retry_wait"
    assert second.quota_exhausted is False
    assert provider.calls == 1
