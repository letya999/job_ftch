import asyncio
import random
from typing import Any

from job_ftch.application.registry import register_bypass


class BehaviorSimBypass:
    """Adds random delays and scroll events to simulate human interaction."""

    def __init__(self, min_delay: float = 0.5, max_delay: float = 2.0) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    async def apply_page(self, page: Any) -> None:
        """Inject a script to simulate human scrolling, or do it via Playwright API."""
        try:
            # We can run a small script to scroll down slightly
            # or just wait randomly to mimic human loading time.
            delay = random.uniform(self._min_delay, self._max_delay)
            await asyncio.sleep(delay)

            # Simple human-like scroll down
            await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
            await asyncio.sleep(random.uniform(0.1, 0.5))
            await page.evaluate("window.scrollBy(0, window.innerHeight / 3)")
            await asyncio.sleep(random.uniform(0.1, 0.5))

            # Move mouse randomly
            width = page.viewport_size["width"] if page.viewport_size else 1024
            height = page.viewport_size["height"] if page.viewport_size else 768
            await page.mouse.move(random.randint(0, width), random.randint(0, height), steps=10)
        except Exception:
            pass


@register_bypass("behavior_sim")
def _create_behavior_sim(
    bypass_config: dict[str, str] | None = None,
) -> BehaviorSimBypass:
    config = bypass_config or {}
    return BehaviorSimBypass(
        min_delay=float(config.get("min_delay", "0.5")),
        max_delay=float(config.get("max_delay", "2.0")),
    )
