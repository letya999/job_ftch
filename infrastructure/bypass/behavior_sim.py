from typing import Any

from application.registry import register_bypass


class BehaviorSimBypass:
    """Adds random delays and scroll events to simulate human interaction.

    Community-maintained. Used together with StealthBrowserBypass.
    """

    def __init__(self, min_delay: float = 0.5, max_delay: float = 2.0) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay

    def configure(self, client: Any) -> Any:
        raise NotImplementedError(
            "BehaviorSimBypass is not yet implemented. "
            "Implement in infrastructure/bypass/behavior_sim.py."
        )


@register_bypass("behavior_sim")
def _create_behavior_sim(
    bypass_config: dict[str, str] | None = None,
) -> BehaviorSimBypass:
    config = bypass_config or {}
    return BehaviorSimBypass(
        min_delay=float(config.get("min_delay", "0.5")),
        max_delay=float(config.get("max_delay", "2.0")),
    )
