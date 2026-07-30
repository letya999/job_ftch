"""Durable-queue ports used by deferred resolution and post-accept lanes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class GraphTaskQueue:
    """A worker-backed queue; production adapters can persist the same task shape."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def put(self, task: Any) -> None:
        await self._queue.put(task)

    async def start(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        if self._worker is not None:
            return

        async def consume() -> None:
            while True:
                await handler(await self._queue.get())
                self._queue.task_done()

        self._worker = asyncio.create_task(consume())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
