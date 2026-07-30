from typing import Any

from job_ftch.application.registry import BypassCapability, register_bypass

try:
    import playwright_stealth

    _STEALTH_AVAILABLE = True
except ImportError:
    playwright_stealth = None
    _STEALTH_AVAILABLE = False

_stealth_async = getattr(playwright_stealth, "stealth_async", None) if _STEALTH_AVAILABLE else None


class StealthBrowserBypass:
    """Applies playwright-stealth patches to Playwright page context."""

    async def apply_http(self, client: Any) -> Any:
        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        args = kwargs.get("args", [])
        if "--disable-blink-features=AutomationControlled" not in args:
            args.append("--disable-blink-features=AutomationControlled")
        if "--disable-features=IsolateOrigins,site-per-process" not in args:
            args.append("--disable-features=IsolateOrigins,site-per-process")
        if "--disable-site-isolation-trials" not in args:
            args.append("--disable-site-isolation-trials")
        if "--disable-web-security" not in args:
            args.append("--disable-web-security")
        kwargs["args"] = args
        return kwargs

    async def apply_page(self, page: Any) -> None:
        if not _STEALTH_AVAILABLE:
            raise ImportError("playwright-stealth is not installed.")
        if callable(_stealth_async):
            await _stealth_async(page)
            return
        stealth_cls = getattr(playwright_stealth, "Stealth", None)
        if stealth_cls is not None:
            stealth_instance = stealth_cls()
            apply_async = getattr(stealth_instance, "apply_stealth_async", None)
            if callable(apply_async):
                await apply_async(page)
                return
        stealth_module = getattr(playwright_stealth, "stealth", None)
        if stealth_module is not None:
            stealth_cls = getattr(stealth_module, "Stealth", None)
            if stealth_cls is not None:
                stealth_instance = stealth_cls()
                apply_async = getattr(stealth_instance, "apply_stealth_async", None)
                if callable(apply_async):
                    await apply_async(page)
                    return
        stealth = getattr(playwright_stealth, "stealth", None)
        if callable(stealth):
            result = stealth(page)
            if hasattr(result, "__await__"):
                await result
            return
        raise AttributeError("playwright_stealth module does not expose stealth hooks")


def _create_stealth(bypass_config: dict[str, Any] | None = None) -> StealthBrowserBypass:
    del bypass_config
    return StealthBrowserBypass()


if _STEALTH_AVAILABLE:
    register_bypass(
        "stealth_browser",
        capability=BypassCapability(
            cost=20,
            browser_family="chromium",
            challenge_actions=frozenset({"passive_js", "generic_challenge"}),
            min_remaining_seconds=5.0,
        ),
    )(_create_stealth)
