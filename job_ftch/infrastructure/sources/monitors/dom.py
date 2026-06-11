"""DOM-based job URL discovery monitor with JS rendering support."""

from __future__ import annotations

import asyncio
import re
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_JOB_KEYWORDS = frozenset({"job", "career", "position", "posting", "opening", "role", "vacancy"})
MAX_URLS = 10_000
_HOSTLIKE_PATH_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}/", re.IGNORECASE)


class LinkExtractor(HTMLParser):
    """Simple stdlib-based link extractor."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value and not value.startswith("#"):
                    absolute = urljoin(self.base_url, value)
                    if absolute.startswith("http"):
                        self.urls.add(absolute)


def _extract_all_links(html: str, base_url: str) -> set[str]:
    extractor = LinkExtractor(base_url)
    extractor.feed(html)
    return set(extractor.urls)


def _build_url_matcher(url_filter: Any) -> re.Pattern[str] | None:
    """Compiles url_filter config into a regex pattern."""
    if not url_filter:
        return None

    pattern_str = ""
    if isinstance(url_filter, str):
        pattern_str = url_filter
    elif isinstance(url_filter, dict) and "include" in url_filter:
        pattern_str = url_filter["include"]

    if pattern_str:
        try:
            return re.compile(pattern_str, re.IGNORECASE)
        except re.error:
            log.warning("dom.invalid_url_filter", pattern=pattern_str)
    return None


def _extract_links_static(
    html: str, base_url: str, url_matcher: re.Pattern[str] | None = None
) -> set[str]:
    """Extract links from static HTML using keyword or regex matching."""
    urls = _extract_all_links(html, base_url)
    if url_matcher:
        urls.update(_extract_regex_urls(html, base_url, url_matcher))
    filtered: set[str] = set()

    for url in urls:
        if url_matcher:
            if url_matcher.search(url):
                filtered.add(url)
        else:
            # fallback to keyword filter
            if any(kw in url.lower() for kw in _JOB_KEYWORDS):
                filtered.add(url)

    return filtered


def _extract_regex_urls(html: str, base_url: str, url_matcher: re.Pattern[str]) -> set[str]:
    """Extract regex-matching URLs from raw HTML, including relative URLs in embedded JSON."""
    candidates: set[str] = set()
    text = unescape(html)

    for match in url_matcher.finditer(text):
        raw = match.group(0)
        if not raw:
            continue
        if raw.startswith("http"):
            candidates.add(raw)
            continue
        if raw.startswith("/"):
            candidates.add(urljoin(base_url, raw))
            continue
        # Regex may match host+path without scheme.
        if "://" not in raw and "/" in raw:
            if _HOSTLIKE_PATH_RE.match(raw):
                scheme = urlparse(base_url).scheme or "https"
                candidates.add(f"{scheme}://{raw.lstrip('/')}")
            else:
                candidates.add(urljoin(base_url, "/" + raw.lstrip("/")))

    return {u for u in candidates if u.startswith("http")}


def _looks_like_detail_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return False
    return any(
        token in segment
        for segment in segments
        for token in ("job", "jobs", "vacancy", "vacancies", "position", "opening", "career")
    )


async def _extract_links_rendered(
    page: Any, board_url: str, config: dict[str, Any], url_matcher: re.Pattern[str] | None = None
) -> set[str]:
    """Extract links from a rendered page using Playwright."""
    from job_ftch.infrastructure.sources.browser_utils import navigate, run_actions

    await navigate(page, board_url, config)

    actions = config.get("actions")
    if actions:
        await run_actions(page, actions)

    settle_seconds = float(config.get("settle_seconds", 0))
    if settle_seconds > 0:
        await asyncio.sleep(settle_seconds)

    # Extract via JS to get the most accurate rendered state
    js_extract = """
    () => Array.from(document.querySelectorAll('a'))
               .map(a => a.href)
               .filter(href => href.startsWith('http'))
    """
    urls: list[str] = await page.evaluate(js_extract)

    filtered: set[str] = set()
    for url in urls:
        if url_matcher:
            if url_matcher.search(url):
                filtered.add(url)
        else:
            if any(kw in url.lower() for kw in _JOB_KEYWORDS):
                filtered.add(url)

    return filtered


async def _paginate_urls(
    board_url: str,
    pagination: dict[str, Any],
    initial_urls: set[str],
    client: httpx.AsyncClient,
    url_matcher: re.Pattern[str] | None = None,
) -> set[str]:
    """Fetch additional pages of URLs via httpx (static pagination)."""
    urls = set(initial_urls)
    param_name = pagination.get("param_name")
    url_template = pagination.get("url_template")
    start = pagination.get("start", 1)
    increment = pagination.get("increment", 1)
    max_pages = pagination.get("max_pages", 5)

    if not (param_name or url_template):
        return urls

    current_page = start
    for _ in range(max_pages):
        if len(urls) >= MAX_URLS:
            break

        if url_template:
            page_url = url_template.format(page=current_page)
        else:
            # Append as query param
            u = list(urlparse(board_url))
            query = dict(parse_qsl(str(u[4])))
            query[str(param_name)] = str(current_page)
            u[4] = urlencode(query)
            page_url = urlunparse(u)

        log.debug("dom.paginate", url=page_url)
        html = await fetch_page_text(page_url, client)
        if not html:
            break

        new_urls = _extract_links_static(html, page_url, url_matcher)
        if not (new_urls - urls):
            # No new URLs found, stop paginating
            break

        urls.update(new_urls)
        current_page += increment

    return urls


async def _expand_listing_urls(
    listing_urls: set[str],
    client: httpx.AsyncClient,
    url_matcher: re.Pattern[str] | None = None,
) -> set[str]:
    """Follow one level of listing pages and extract job/detail URLs from them."""
    expanded: set[str] = set()
    for listing_url in listing_urls:
        if len(expanded) >= MAX_URLS:
            break
        html = await fetch_page_text(listing_url, client)
        if not html:
            continue
        expanded.update(_extract_links_static(html, listing_url, url_matcher))
    return expanded


async def discover(spec: Any, client: httpx.AsyncClient, auth: Any = None) -> set[str]:
    board_url = spec.url
    config = spec.monitor_config
    render = config.get("render", False)
    url_matcher = _build_url_matcher(config.get("url_filter"))
    pagination = config.get("pagination")
    expand_links = config.get("expand_links")
    all_urls: set[str] = set()

    if render:
        try:
            from playwright.async_api import async_playwright

            from job_ftch.infrastructure.sources.browser_utils import BROWSER_KEYS, open_page
        except ImportError as exc:
            raise RuntimeError(
                "playwright is required for DOM monitor with render=true. "
                "Install: uv add playwright --optional browser && playwright install chromium"
            ) from exc

        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
        async with async_playwright() as pw, open_page(pw, browser_config) as page:
            urls = await _extract_links_rendered(page, board_url, config, url_matcher)
    else:
        html = await fetch_page_text(board_url, client)
        if not html:
            log.warning("dom.fetch_failed", board_url=board_url)
            return set()
        if expand_links:
            all_urls = _extract_all_links(html, board_url)
        urls = _extract_links_static(html, board_url, url_matcher)

    if pagination:
        urls = await _paginate_urls(board_url, pagination, urls, client, url_matcher)

    if expand_links:
        patterns = expand_links if isinstance(expand_links, list) else [expand_links]
        listing_urls = {
            url
            for url in (all_urls or urls)
            if any(re.search(pattern, url, re.IGNORECASE) for pattern in patterns)
        }
        if listing_urls:
            urls.update(await _expand_listing_urls(listing_urls, client, url_matcher))

    # Exclude the board URL itself
    normalized_board = board_url.rstrip("/")
    urls = {u for u in urls if u.rstrip("/") != normalized_board}

    if config.get("include_self_url") or (
        config.get("include_if_detail_page", True)
        and not urls
        and _looks_like_detail_page(board_url)
    ):
        urls.add(board_url)

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    # URL filter support in can_handle would require the config, but we only have URL here.
    # We'll just check if we can find any links with keywords as a smoke test.
    if client is None:
        return None
    html = await fetch_page_text(url, client)
    if not html:
        return None

    urls = _extract_links_static(html, url)
    if urls:
        return {"urls": len(urls)}
    return None


register_monitor("dom", discover, cost=50, rich=False, can_handle=can_handle)
