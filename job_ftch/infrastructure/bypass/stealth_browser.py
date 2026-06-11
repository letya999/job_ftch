from typing import Any

from job_ftch.application.registry import register_bypass

try:
    import playwright_stealth

    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False


class StealthBrowserBypass:
    """Applies playwright-stealth patches to Playwright page context."""

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # Append arguments to disable automation features
        args = kwargs.get("args", [])
        if "--disable-blink-features=AutomationControlled" not in args:
            args.append("--disable-blink-features=AutomationControlled")
        kwargs["args"] = args
        return kwargs

    async def apply_page(self, page: Any) -> None:
        if not _STEALTH_AVAILABLE:
            raise ImportError("playwright-stealth is not installed.")
        # Async stealth injection
        await playwright_stealth.stealth_async(page)


@register_bypass("stealth_browser")
def _create_stealth(bypass_config: dict[str, str] | None = None) -> StealthBrowserBypass:
    del bypass_config
    return StealthBrowserBypass()
