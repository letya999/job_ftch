"""Loop-local global concurrency limits for expensive career-site operations."""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_limiters: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, int], asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _limiter(name: str, capacity: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    loop_limiters = _limiters.setdefault(loop, {})
    limiter = loop_limiters.get((name, capacity))
    if limiter is None:
        limiter = asyncio.Semaphore(capacity)
        loop_limiters[(name, capacity)] = limiter
    return limiter


@asynccontextmanager
async def detail_slot(capacity: int) -> AsyncIterator[None]:
    limiter = _limiter("detail", capacity)
    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    await await_with_source_deadline(limiter.acquire())
    try:
        yield
    finally:
        limiter.release()


@asynccontextmanager
async def browser_slot(capacity: int) -> AsyncIterator[None]:
    limiter = _limiter("browser", capacity)
    # Browser startup is globally bounded.  Acquiring its queue must obey the
    # per-source deadline too; otherwise queued sources can outlive their
    # budget and create late browser work after they are already terminal.
    from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

    await await_with_source_deadline(limiter.acquire())
    try:
        yield
    finally:
        limiter.release()
