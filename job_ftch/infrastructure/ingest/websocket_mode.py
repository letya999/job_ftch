from collections.abc import Awaitable, Callable
from typing import Any

from job_ftch.infrastructure.sources.base import Source


class WebSocketMode:
    """IngestMode for persistent WebSocket sources."""

    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
