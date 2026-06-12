from typing import Any, cast

import structlog

from job_ftch.application.registry import register_bypass, resolve_bypass

logger = structlog.get_logger("job_ftch.bypass.adaptive")


class AdaptiveBypassManager:
    """Manages tiered escalation of bypass strategies."""

    # Tier 0: noop (fastest)
    # Tier 1: curl_stealth (TLS masking, fast)
    # Tier 2: stealth_browser (Playwright + stealth JS)
    # Tier 3: cloak (C++ compiled stealth browser)
    TIERS = ["noop", "curl_stealth", "stealth_browser", "cloak"]

    def __init__(self, bypass_config: dict[str, Any] | None = None) -> None:
        self.config = bypass_config or {}
        self.current_tier_index = 0
        self._current_strategy: Any = None
        self._ensure_strategy()

    def _ensure_strategy(self) -> None:
        name = self.TIERS[self.current_tier_index]
        logger.debug("adaptive_bypass_init_tier", tier=self.current_tier_index, name=name)
        self._current_strategy = resolve_bypass(name, bypass_config=self.config)

    def escalate(self) -> bool:
        """Escalate to the next tier. Returns True if successful, False if max tier reached."""
        if self.current_tier_index < len(self.TIERS) - 1:
            self.current_tier_index += 1
            self._ensure_strategy()
            return True
        return False

    @property
    def current_name(self) -> str:
        return self.TIERS[self.current_tier_index]

    async def apply_http(self, client: Any) -> Any:
        return await self._current_strategy.apply_http(client)

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return cast("dict[str, Any]", self._current_strategy.apply_browser_args(kwargs))

    async def apply_page(self, page: Any) -> None:
        await self._current_strategy.apply_page(page)


@register_bypass("auto")
def _create_adaptive(bypass_config: dict[str, str] | None = None) -> AdaptiveBypassManager:
    return AdaptiveBypassManager(bypass_config)
