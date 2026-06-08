from collections.abc import AsyncIterator
from typing import Any


class HardScraperSource:
    """Full browser scraping pipeline: sniffer -> scraper -> parser -> behavior sim.

    Community-maintained. Requires playwright + bypass infrastructure.
    See docs/sources/hard_scraper.md for implementation contract.
    """

    def __init__(self, spec: Any, auth: Any, bypass: Any | None = None) -> None:
        raise NotImplementedError(
            "HardScraperSource is not implemented. "
            "This is a community-maintained component. "
            "See docs/sources/hard_scraper.md for the implementation contract."
        )

    async def fetch(self) -> AsyncIterator[Any]:
        raise NotImplementedError
        yield
