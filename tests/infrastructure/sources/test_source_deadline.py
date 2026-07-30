from __future__ import annotations

import asyncio
import gc

import pytest

from job_ftch.infrastructure.sources.source_deadline import (
    await_with_source_deadline,
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
