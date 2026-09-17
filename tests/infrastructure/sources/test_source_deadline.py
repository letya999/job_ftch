from __future__ import annotations

import asyncio
import gc

import pytest

from job_ftch.infrastructure.sources.source_deadline import (
    await_with_source_deadline,
    remaining_source_seconds,
    reset_source_deadline,
    set_source_deadline,
)


@pytest.mark.asyncio
async def test_expired_deadline_closes_created_coroutine_without_warning() -> None:
    async def pending_operation() -> None:
        await asyncio.sleep(1)

    token = set_source_deadline(asyncio.get_running_loop().time() - 1)
    try:
        with pytest.raises(TimeoutError, match="source deadline exhausted"):
            await await_with_source_deadline(pending_operation())
    finally:
        reset_source_deadline(token)
    # Force the coroutine destructor during the test: an unclosed coroutine
    # would emit RuntimeWarning and fail under pytest's warning policy.
    gc.collect()


@pytest.mark.asyncio
async def test_timeout_cancels_and_drains_child_operation() -> None:
    cancelled = asyncio.Event()

    async def pending_operation() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    token = set_source_deadline(asyncio.get_running_loop().time() + 0.01)
    try:
        with pytest.raises(TimeoutError, match="source deadline exhausted"):
            await await_with_source_deadline(pending_operation())
    finally:
        reset_source_deadline(token)

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_attempt_timeout_does_not_wait_for_uncancellable_drain() -> None:
    async def hung_operation() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(60)

    started = asyncio.get_running_loop().time()
    before = set(asyncio.all_tasks())
    token = set_source_deadline(started + 2.0)
    try:
        with pytest.raises(TimeoutError, match="source attempt timeout"):
            await await_with_source_deadline(hung_operation(), timeout=0.05)
        assert asyncio.get_running_loop().time() - started < 1.0
    finally:
        reset_source_deadline(token)
        leftover = [task for task in asyncio.all_tasks() if task not in before and not task.done()]
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.gather(*leftover, return_exceptions=True)


@pytest.mark.asyncio
async def test_attempt_timeout_does_not_consume_full_source_deadline() -> None:
    started = asyncio.get_running_loop().time()
    token = set_source_deadline(started + 2.0)
    try:
        with pytest.raises(TimeoutError, match="source attempt timeout"):
            await await_with_source_deadline(asyncio.sleep(5), timeout=0.05)
        assert remaining_source_seconds() is not None
        assert remaining_source_seconds() > 1.0
    finally:
        reset_source_deadline(token)
    assert asyncio.get_running_loop().time() - started < 0.5
