from collections.abc import Awaitable, Callable
from typing import Any

from infrastructure.sources.base import Source


class WebhookMode:
    """IngestMode for push-via-HTTP sources (WebhookSource)."""

    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
