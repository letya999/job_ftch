"""Shared helpers for discover-oriented site parsers."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

from job_ftch.domain import SourceKind
from job_ftch.infrastructure.bypass.failure_signal import is_challenge_body
from job_ftch.infrastructure.sources.browser_utils import (
    BROWSER_KEYS,
    safe_content,
    scroll_to_bottom,
)
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    import re
    from collections.abc import Callable

    import httpx

    from job_ftch.config import Settings
    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def is_challenge_response(html_text: str) -> bool:
    """Detect if an HTTP response is a captcha/anti-bot challenge page.

    Generic helper for site parsers to detect challenge pages without
    hardcoding vendor-specific markers. Uses the unified failure_signal
    classifier which covers Cloudflare, DataDome, hCaptcha, reCAPTCHA,
    SmartCaptcha, DDOS-GUARD, etc.
    """
    if not html_text:
        return False
    return is_challenge_body(html_text)


async def safe_fetch(
    client: Any,
    url: str,
    *,
    follow_redirects: bool = True,
) -> httpx.Response:
    """Fetch a page with retry semantics and fail on non-2xx status."""
    response = await fetch_with_retry(client, url, follow_redirects=follow_redirects)
    response.raise_for_status()
    return response


def extract_urls_with_limit(
    html: str,
    pattern: re.Pattern[str],
    base_url: str,
    limit: int,
    seen: set[str] | None = None,
) -> list[str]:
    """Extract canonical absolute URLs from regex matches with dedup and cap."""
    seen_urls = seen if seen is not None else set()
    urls: list[str] = []
    for match in pattern.findall(html):
        raw_match = match[0] if isinstance(match, tuple) else match
        if not isinstance(raw_match, str):
            raw_match = str(raw_match)
        url = urljoin(base_url, raw_match)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


SEARCH_QUERY_PARAM_NAMES: tuple[str, ...] = (
    "text",
    "q",
    "qs",
    "query",
    "search",
    "keywords",
    "keyword",
    "s",
)


def normalize_search_keywords(keywords: Any, *, cap: int = 12) -> list[str]:
    """Clean target-role keywords for search: strip, collapse spaces, dedupe, cap.

    Dedup is case-insensitive but the first-seen spelling is preserved so the
    query stays readable. The cap bounds query length/complexity on aggregators
    that reject very long ``text`` expressions.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in keywords or ():
        if not isinstance(raw, str):
            continue
        text = " ".join(raw.split()).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= cap:
            break
    return result


def with_query_params(url: str, params: dict[str, str]) -> str:
    """Return ``url`` with ``params`` merged into its query string (overwrite)."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def url_has_search_query(url: str, param_names: Any = SEARCH_QUERY_PARAM_NAMES) -> bool:
    """True if ``url`` already carries a non-empty known search parameter.

    Used for idempotent spec expansion: a source whose URL was authored with an
    explicit query (e.g. ``?text=LLM+engineer``) must never be re-expanded.
    """
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    names = {str(name).casefold() for name in param_names}
    return any(key.casefold() in names and value.strip() for key, value in query.items())


def parse_detail_html_generic(
    html: str,
    url: str,
    source_name: str,
    *,
    title_sel: str = "h1",
    body_sel: tuple[str, ...] | str = ("main", "article", "body"),
    id_extractor: Callable[[str], str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RawItem | None:
    """Build a minimal RawItem from a detail page when generic scraping suffices."""
    tree = HTMLParser(html)
    title_node = tree.css_first(title_sel)
    title = " ".join((title_node.text(strip=True) if title_node else "").split())
    if not title:
        title_tag = tree.css_first("title")
        if title_tag is not None:
            title = " ".join(title_tag.text(strip=True).split())
    if not title:
        return None

    selectors = (body_sel,) if isinstance(body_sel, str) else body_sel
    body = ""
    for selector in selectors:
        node = tree.css_first(selector)
        if node is None:
            continue
        body = " ".join(node.text(separator=" ", strip=True).split())
        if body:
            break
    if not body:
        meta_description = tree.css_first('meta[name="description"]')
        if meta_description is not None:
            body = " ".join(str(meta_description.attributes.get("content", "") or "").split())

    external_id = (
        id_extractor(url) if id_extractor is not None else url.rstrip("/").rsplit("/", 1)[-1]
    )
    if not external_id:
        external_id = url

    item_metadata = dict(metadata or {})
    item_metadata.setdefault("job_url", url)
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=url,
        text="\n".join(part for part in (title, body) if part),
        metadata=item_metadata,
    )


async def browser_scroll_collect_urls(
    page: Any,
    base_url: str,
    pattern: re.Pattern[str],
    *,
    limit: int,
    scroll_loops: int,
    pause_sec: float,
    scroll_px: int = 3000,
    stale_rounds: int = 4,
) -> list[str]:
    """Collect matching detail URLs from a hydrated browser page."""
    seen: set[str] = set()
    collected: list[str] = []

    async def _append_discovered() -> None:
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        hrefs = await await_with_source_deadline(
            page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map((a) => a.href)"
            )
        )
        for href in hrefs:
            if not isinstance(href, str) or not pattern.search(href):
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            seen.add(url)
            collected.append(url)
            if len(collected) >= limit:
                return

        html = await safe_content(page)
        for url in extract_urls_with_limit(html, pattern, base_url, limit, seen=seen):
            collected.append(url)
            if len(collected) >= limit:
                return

    await _append_discovered()
    previous_count = len(collected)
    stale_count = 0
    for _ in range(scroll_loops):
        if len(collected) >= limit:
            break
        await scroll_to_bottom(
            page,
            max_scrolls=1,
            scroll_pause_seconds=pause_sec,
            pixel_step=scroll_px,
            return_to_top=False,
        )
        await _append_discovered()
        if len(collected) == previous_count:
            stale_count += 1
            if stale_count >= stale_rounds:
                break
        else:
            stale_count = 0
            previous_count = len(collected)
    return collected[:limit]


def resolve_browser_config(
    spec: CareerSiteSpec,
    bypass_strategy: Any = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge browser settings from parser defaults and source spec."""
    del bypass_strategy
    browser_config = dict(defaults or {})
    for key, value in spec.monitor_config.items():
        if key in BROWSER_KEYS:
            browser_config[key] = value
    browser_config.setdefault("headless", True)
    browser_config.setdefault("stealth", True)
    browser_config.setdefault("wait", "domcontentloaded")
    return browser_config


async def handle_bypass_escalation(
    bypass_strategy: Any,
    exc: Exception,
    url: str,
    log: Any,
) -> bool:
    """Try to escalate bypass strategy after a parser-level failure."""
    if bypass_strategy is None or not hasattr(bypass_strategy, "escalate"):
        return False
    escalated = bypass_strategy.escalate()
    if inspect.isawaitable(escalated):
        escalated = await escalated
    if escalated:
        log.warning("site_parser_bypass_escalated", url=url, error=str(exc))
    return bool(escalated)


def effective_limit(spec: CareerSiteSpec, settings: Settings) -> int:
    """Mirror CareerSiteSource detail-limit selection for parser-side discovery."""
    explicit = spec.detail_limit or spec.limit
    if explicit:
        return explicit
    if spec.freshness_cutoff_utc is not None:
        return settings.career_site_window_max_details
    return settings.career_site_default_detail_limit or settings.career_site_default_limit
