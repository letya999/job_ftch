"""CompositeSource: fan-in over multiple Source adapters."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from application.contracts import Source
    from domain import QuarantinedRawItem, RawItem

logger = logging.getLogger("job_ftch.composite_source")


class CompositeSource:
    """Fan-in source that aggregates items from multiple child sources.

    Sequential mode (concurrency=1): yields items from each child in order.
    Parallel mode (concurrency>1): uses asyncio.TaskGroup + bounded Queue.
    A failing child records an error and does not abort others.
    """

    def __init__(
        self,
        sources: Sequence[Source[RawItem]],
        *,
        concurrency: int = 1,
        queue_capacity: int = 100,
    ) -> None:
        if not sources:
            raise ValueError("CompositeSource requires at least one child source.")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1.")
        self._sources = list(sources)
        self._concurrency = concurrency
        self._queue_capacity = queue_capacity
        self.failed_sources: int = 0

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        if self._concurrency == 1:
            async for item in self._fetch_sequential():
                yield item
        else:
            async for item in self._fetch_parallel():
                yield item

    async def _fetch_sequential(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        for source in self._sources:
            try:
                async for item in source.fetch():
                    yield item
            except Exception:
                self.failed_sources += 1
                logger.exception("child_source_failed", extra={"source": repr(source)})

    async def _fetch_parallel(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        _SENTINEL = object()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_capacity)

        async def _drain_one(source: Source[RawItem]) -> None:
            try:
                async for item in source.fetch():
                    await queue.put(item)
            except Exception:
                self.failed_sources += 1
                logger.exception("child_source_failed", extra={"source": repr(source)})

        async def _run_all() -> None:
            async with asyncio.TaskGroup() as tg:
                for source in self._sources:
                    tg.create_task(_drain_one(source))
            await queue.put(_SENTINEL)

        producer = asyncio.create_task(_run_all())
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]
        finally:
            if not producer.done():
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass
