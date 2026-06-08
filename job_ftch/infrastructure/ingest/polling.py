from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.contracts import IngestMode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from job_ftch.application.contracts import Source


class PollingMode(IngestMode):
    """
    Drive source fetch and call on_item for each yielded item.
    Simple sequential iteration.
    """

    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        async for item in source.fetch():
            await on_item(item)
