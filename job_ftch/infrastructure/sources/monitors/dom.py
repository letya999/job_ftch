"""DOM-based job URL discovery monitor ported from jobseek."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("job_ftch.monitors.dom")

_JOB_KEYWORDS = frozenset(
    {"job", "career", "position", "posting", "opening", "role", "vacancy"}
)
MAX_URLS = 50_000


def _extract_links(html: str, base_url: str, selector: str = "a") -> set[str]:
    parser = LexborHTMLParser(html)
    urls: set[str] = set()
    for node in parser.css(selector):
        href = node.attributes.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http"):
            continue
        # keyword filter
        if any(kw in absolute.lower() for kw in _JOB_KEYWORDS):
            urls.add(absolute)
    return urls


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    board_url = spec.url
    config = spec.monitor_config
    selector = config.get("selector", "a")

    html = await fetch_page_text(board_url, client)
    if not html:
        logger.warning("dom.fetch_failed", board_url=board_url)
        return set()

    urls = _extract_links(html, board_url, selector)

    # Exclude the board URL itself
    normalized_board = board_url.rstrip("/")
    urls = {u for u in urls if u.rstrip("/") != normalized_board}

    if len(urls) > MAX_URLS:
        logger.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    if client is None:
        return None
    html = await fetch_page_text(url, client)
    if not html:
        return None

    urls = _extract_links(html, url)
    if urls:
        return {"urls": len(urls)}
    return None


register_monitor("dom", discover, cost=100, rich=False, can_handle=can_handle)
