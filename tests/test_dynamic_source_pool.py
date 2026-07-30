from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.composite import CompositeSource

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _build_item(source_name: str, external_id: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.DEBUG,
        source_name=source_name,
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        text=external_id,
        metadata={},
    )


class FastSource:
    def __init__(self, source_name: str) -> None:
        self.source_kind = SourceKind.DEBUG
        self.source_name = source_name

    async def fetch(self) -> AsyncIterator[RawItem]:
        yield _build_item(self.source_name, self.source_name)


class BlockingSource:
    def __init__(
        self,
        source_name: str,
        started: asyncio.Event,
        release: asyncio.Event,
        *,
        close_delay: float = 0.0,
    ) -> None:
        self.source_kind = SourceKind.DEBUG
        self.source_name = source_name
        self._started = started
        self._release = release
        self._close_delay = close_delay

    async def fetch(self) -> AsyncIterator[RawItem]:
        self._started.set()
        await self._release.wait()
        if self._close_delay:
            await asyncio.sleep(self._close_delay)
        yield _build_item(self.source_name, f"{self.source_name}-done")


class PartiallyBlockingSource(BlockingSource):
    async def fetch(self) -> AsyncIterator[RawItem]:
        self._started.set()
        yield _build_item(self.source_name, f"{self.source_name}-first")
        await self._release.wait()


class DelayedCancellationSource:
    """Simulate a detail worker that needs extra time to clean up."""

    def __init__(
        self,
        source_name: str,
        release: asyncio.Event,
        cancelled_again: asyncio.Event,
    ) -> None:
        self.source_kind = SourceKind.DEBUG
        self.source_name = source_name
        self._release = release
        self._cancelled_again = cancelled_again

    async def fetch(self) -> AsyncIterator[RawItem]:
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            try:
                await self._release.wait()
            except asyncio.CancelledError:
                self._cancelled_again.set()
                raise
        if False:
            yield _build_item(self.source_name, "unreachable")


class DetachedCancellationSource:
    """Consumes two cancellation signals before its final teardown signal."""

    def __init__(self, source_name: str, closed: asyncio.Event) -> None:
        self.source_kind = SourceKind.DEBUG
        self.source_name = source_name
        self._closed = closed
        self._cancellations = 0

    async def fetch(self) -> AsyncIterator[RawItem]:
        try:
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # The hard-deadline path has two bounded cancellation
                    # attempts.  The composite shutdown must still drain a
                    # producer which survives those two signals.
                    self._cancellations += 1
                    if self._cancellations >= 3:
                        self._closed.set()
                        raise
        except asyncio.CancelledError:
            raise
        if False:
            yield _build_item(self.source_name, "unreachable")


@pytest.mark.asyncio
async def test_dynamic_pool_evicts_slow_source_and_keeps_draining_fast_lane() -> None:
    slow_started = asyncio.Event()
    slow_release = asyncio.Event()
    composite = CompositeSource(
        [
            BlockingSource("slow", slow_started, slow_release),
            FastSource("fast-a"),
            FastSource("fast-b"),
        ],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.05,
        hard_deadline_seconds=0.5,
        overflow_concurrency=1,
    )

    iterator = composite.fetch().__aiter__()
    first = await asyncio.wait_for(anext(iterator), timeout=0.2)
    second = await asyncio.wait_for(anext(iterator), timeout=0.2)

    assert {first.external_id, second.external_id} == {"fast-a", "fast-b"}
    assert slow_started.is_set()

    result = composite.source_results["debug:slow"]
    assert result.evicted is False
    await asyncio.sleep(0.08)
    assert composite.overflow_workers_started == 1

    slow_release.set()
    third = await asyncio.wait_for(anext(iterator), timeout=0.5)
    assert third.external_id == "slow-done"

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(iterator), timeout=0.2)


@pytest.mark.asyncio
async def test_dynamic_pool_dead_letters_source_after_hard_deadline() -> None:
    slow_started = asyncio.Event()
    composite = CompositeSource(
        [BlockingSource("slow", slow_started, asyncio.Event())],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.05,
        hard_deadline_seconds=0.1,
        overflow_concurrency=1,
    )

    items = [item async for item in composite.fetch()]

    assert items == []
    assert slow_started.is_set()
    result = composite.source_results["debug:slow"]
    assert result.evicted is True
    assert result.eviction_kind == "hard_deadline"


@pytest.mark.asyncio
async def test_sequential_pool_enforces_the_same_hard_deadline() -> None:
    started = asyncio.Event()
    composite = CompositeSource(
        [BlockingSource("slow", started, asyncio.Event())],
        concurrency=1,
        soft_deadline_seconds=0.02,
        hard_deadline_seconds=0.05,
    )

    assert [item async for item in composite.fetch()] == []

    result = composite.source_results["debug:slow"]
    assert started.is_set()
    assert result.eviction_kind == "hard_deadline"
    assert result.deadline_exceeded is True


@pytest.mark.asyncio
async def test_sequential_pool_marks_emitted_items_partial_after_deadline() -> None:
    started = asyncio.Event()
    composite = CompositeSource(
        [PartiallyBlockingSource("slow", started, asyncio.Event())],
        concurrency=1,
        soft_deadline_seconds=0.02,
        hard_deadline_seconds=0.05,
    )

    assert [item.external_id async for item in composite.fetch()] == ["slow-first"]

    result = composite.source_results["debug:slow"]
    assert result.partial is True
    assert result.completion_state == "partial"


@pytest.mark.asyncio
async def test_legacy_parallel_pool_enforces_the_same_hard_deadline() -> None:
    started = asyncio.Event()
    composite = CompositeSource(
        [BlockingSource("slow", started, asyncio.Event()), FastSource("fast")],
        concurrency=2,
        dynamic_enabled=False,
        soft_deadline_seconds=0.02,
        hard_deadline_seconds=0.05,
    )

    items = [item async for item in composite.fetch()]

    assert [item.external_id for item in items] == ["fast"]
    result = composite.source_results["debug:slow"]
    assert started.is_set()
    assert result.eviction_kind == "hard_deadline"
    assert result.deadline_exceeded is True
    assert result.completion_state == "partial"


@pytest.mark.asyncio
async def test_dynamic_pool_hard_deadline_is_total_source_budget() -> None:
    started = asyncio.Event()
    composite = CompositeSource(
        [BlockingSource("slow", started, asyncio.Event())],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.1,
        hard_deadline_seconds=0.3,
        overflow_concurrency=1,
    )

    loop = asyncio.get_running_loop()
    began_at = loop.time()
    assert [item async for item in composite.fetch()] == []
    elapsed = loop.time() - began_at

    assert started.is_set()
    assert composite.source_results["debug:slow"].eviction_kind == "hard_deadline"
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_dynamic_pool_does_not_wait_for_delayed_cancellation_cleanup() -> None:
    release = asyncio.Event()
    cancelled_again = asyncio.Event()
    composite = CompositeSource(
        [DelayedCancellationSource("slow", release, cancelled_again)],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.02,
        hard_deadline_seconds=0.06,
        overflow_concurrency=1,
    )

    async def _collect_items() -> list[RawItem]:
        return [item async for item in composite.fetch()]

    assert await asyncio.wait_for(_collect_items(), timeout=0.3) == []
    assert composite.source_results["debug:slow"].eviction_kind == "hard_deadline"
    await asyncio.wait_for(cancelled_again.wait(), timeout=0.3)
    assert not release.is_set()


@pytest.mark.asyncio
async def test_dynamic_pool_drains_a_detached_producer_during_shutdown() -> None:
    closed = asyncio.Event()
    composite = CompositeSource(
        [DetachedCancellationSource("slow", closed)],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.02,
        hard_deadline_seconds=0.06,
        hard_cancel_grace_seconds=0.01,
        overflow_concurrency=1,
    )

    assert [item async for item in composite.fetch()] == []
    assert composite.source_results["debug:slow"].eviction_kind == "hard_deadline"
    assert closed.is_set()


def test_dynamic_pool_rejects_non_increasing_deadlines() -> None:
    with pytest.raises(ValueError, match="soft_deadline_seconds must be smaller"):
        CompositeSource(
            [FastSource("source")],
            soft_deadline_seconds=1.0,
            hard_deadline_seconds=1.0,
        )


@pytest.mark.asyncio
async def test_dynamic_pool_keeps_overflow_lazy_when_all_sources_are_fast() -> None:
    composite = CompositeSource(
        [FastSource("fast-a"), FastSource("fast-b")],
        concurrency=2,
        dynamic_enabled=True,
        soft_deadline_seconds=0.05,
        hard_deadline_seconds=0.1,
        overflow_concurrency=2,
    )

    items = [item async for item in composite.fetch()]

    assert sorted(item.external_id for item in items) == ["fast-a", "fast-b"]
    assert composite.overflow_workers_started == 0


@pytest.mark.asyncio
async def test_dynamic_pool_flag_off_uses_legacy_parallel_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False
    original = CompositeSource._fetch_parallel

    async def _wrapped(self: CompositeSource) -> AsyncIterator[RawItem]:
        nonlocal called
        called = True
        async for item in original(self):
            yield item

    monkeypatch.setattr(CompositeSource, "_fetch_parallel", _wrapped)
    composite = CompositeSource(
        [FastSource("fast-a"), FastSource("fast-b")],
        concurrency=2,
        dynamic_enabled=False,
    )

    items = [item async for item in composite.fetch()]

    assert called is True
    assert sorted(item.external_id for item in items) == ["fast-a", "fast-b"]
