from typing import Any

from application.registry import register_bypass

try:
    import playwright_stealth

    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False


class StealthBrowserBypass:
    """Applies playwright-stealth patches to Playwright page context.

    Install: pip install playwright-stealth
    Community-maintained: see infrastructure/bypass/stealth_browser.py
    """

    def configure(self, client: Any) -> Any:
        if not _STEALTH_AVAILABLE:
            raise ImportError(
                "playwright-stealth is not installed. Run: pip install playwright-stealth"
            )
        # When a Playwright page is passed, apply stealth patches
        if hasattr(client, "add_init_script"):
            playwright_stealth.stealth_sync(client)
        return client


@register_bypass("stealth_browser")
def _create_stealth() -> StealthBrowserBypass:
    return StealthBrowserBypass()
