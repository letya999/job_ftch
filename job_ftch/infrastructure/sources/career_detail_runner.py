"""One registered detail scraper attempt with browser-capability enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from job_ftch.application.registry import resolve_scraper

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from job_ftch.domain.site_models import ScrapedPostingPayload


async def run_scraper_attempt(
    scraper_name: str,
    *,
    url: str,
    base_config: dict[str, Any],
    prefetched_html: str | None,
    http: Any,
    fetch_browser_html: Callable[[], Awaitable[str | None]],
    resolver: Callable[[str], Any] = resolve_scraper,
) -> tuple[ScrapedPostingPayload | None, str | None]:
    """Execute one scraper and return both its payload and effective HTML."""
    entry = resolver(scraper_name)
    if getattr(entry, "needs_browser", False) and not prefetched_html:
        prefetched_html = await fetch_browser_html()
        if not prefetched_html:
            return None, None
    config = dict(base_config)
    if prefetched_html and not config and entry.can_handle is not None:
        inferred = entry.can_handle([prefetched_html])
        if isinstance(inferred, dict):
            config = inferred
    if prefetched_html:
        config["prefetched_html"] = prefetched_html
    payload = await entry.factory(url, config, http)
    return cast("ScrapedPostingPayload | None", payload), prefetched_html
