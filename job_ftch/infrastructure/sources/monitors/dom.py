"""DOM-based job URL discovery monitor with JS rendering support."""

from __future__ import annotations

import asyncio
import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog

from job_ftch.application.registry import register_monitor
from job_ftch.domain.site_models import MonitorResult
from job_ftch.infrastructure.sources.monitors.shared import fetch_page_text
from job_ftch.infrastructure.sources.url_scoring import (
    is_probable_job_url,
    looks_like_listing_url,
    rank_job_urls,
    score_job_url,
)

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()


def _is_probable_job_link(url: str) -> bool:
    return is_probable_job_url(url)


MAX_URLS = 10_000
_MAX_HTML_FOR_BOARD_CHECK = 500_000
# One-level expansion is a recovery path, not a site crawl.  It is bounded so
# a navigation page with hundreds of category links cannot consume a source's
# deadline before any detail page is fetched.
MAX_AUTO_EXPANDED_LISTINGS = 20
_HOSTLIKE_PATH_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}/", re.IGNORECASE)
_CONFIRMED_EMPTY_BOARD_RE = re.compile(
    r"(?:\bno\s+(?:open\s+)?(?:job|jobs|vacanc(?:y|ies)|position|positions)\s*"
    r"(?:available|at\s+(?:the\s+)?moment|for\s+now)?\b|"
    r"\bthere\s+are\s+currently\s+no\s+(?:open\s+)?(?:job|jobs|vacanc(?:y|ies)|position|positions)\b|"
    r"\b(?:вакансий|вакансии)\s+(?:нет|не\s+найдено)\b)",
    re.IGNORECASE,
)
_BOARD_GONE_RE = re.compile(
    r"\b(?:careers?|jobs?|vacanc(?:y|ies))\b.{0,160}\b(?:under\s+construction|coming\s+soon)\b"
    r"|\b(?:under\s+construction|coming\s+soon)\b.{0,160}\b(?:careers?|jobs?|vacanc(?:y|ies))\b"
    r"|\b(?:under\s+construction|coming\s+soon)\b.{0,240}\b(?:please\s+get\s+back|"
    r"something\s+interesting\s+is\s+about)\b"
    r"|\bunder-construction(?:__[\w-]+)?\b.*?\bplease\s+get\s+back\s+soon\b",
    re.IGNORECASE | re.DOTALL,
)
_UNAVAILABLE_LISTING_PLATFORM_RE = re.compile(
    r"\bplatforma\s+nu\s+este\s+disponibil[ăa]\b.{0,500}"
    r"\b(?:anunțurile|anunturile)\b",
    re.IGNORECASE | re.DOTALL,
)


def _repair_legacy_href(value: str) -> str:
    """Recover a CP1251 href preserved as UTF-8 surrogate escapes.

    Some legacy sites declare UTF-8 for the document but write only their
    link attributes in CP1251.  ``surrogateescape`` keeps those bad bytes
    intact; repairing them here is limited to href values and does not alter
    normal visible page text.
    """
    if not any("\udc80" <= char <= "\udcff" for char in value):
        return value
    try:
        return value.encode("utf-8", errors="surrogateescape").decode("cp1251")
    except UnicodeError:
        return value


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
                    absolute = urljoin(self.base_url, _repair_legacy_href(value))
                    if absolute.startswith("http"):
                        self.urls.add(absolute)


def _extract_all_links(html: str, base_url: str) -> set[str]:
    extractor = LinkExtractor(base_url)
    extractor.feed(html)
    return set(extractor.urls)


def _visible_text_for_board_check(html: str) -> str:
    """Strip script/style content from the full document before truncating.

    Truncating raw HTML first (as this used to do) can slice a large inline
    script (e.g. a bundled i18n/translation JSON blob) in half, leaving its
    unclosed `<script>` content to leak through the stripping regex as if it
    were visible page text. Large SPA frameworks routinely embed strings like
    "No job offers found" in such payloads for their 404/empty-state
    templates, which then falsely triggered confirmed-empty detection on
    boards that were actually full of listings. Stripping scripts/styles on
    the untruncated document keeps every `<script>...</script>` pair intact.
    """
    visible = unescape(
        re.sub(
            r"<\s*(?:script|style)\b[^>]*>.*?<\s*/\s*(?:script|style)\b[^>]*>",
            " ",
            html,
            flags=re.I | re.S,
        )
    )
    visible = re.sub(r"<[^>]+>", " ", visible)
    return " ".join(visible.split())[:_MAX_HTML_FOR_BOARD_CHECK]


def _has_confirmed_empty_board_message(html: str) -> bool:
    """Recognise an explicit empty-board statement, not merely a sparse page."""
    return bool(_CONFIRMED_EMPTY_BOARD_RE.search(_visible_text_for_board_check(html)))


def _has_board_gone_message(html: str) -> bool:
    """Recognise an explicitly retired/unfinished career board.

    This is distinct from an empty board: there is no valid listing to crawl
    until the operator publishes it, so retries and detail scraping cannot
    recover vacancies.
    """
    check_html = html[:_MAX_HTML_FOR_BOARD_CHECK]
    if re.search(
        r"\bunder-construction(?:__[\w-]+)?\b.*?\bplease\s+get\s+back\s+soon\b",
        check_html,
        re.IGNORECASE | re.DOTALL,
    ):
        return True
    if _UNAVAILABLE_LISTING_PLATFORM_RE.search(check_html):
        return True
    return bool(_BOARD_GONE_RE.search(_visible_text_for_board_check(html)))


_JSONLD_SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _iter_jsonld_urls(node: Any, base_url: str) -> set[str]:
    urls: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "url" and isinstance(value, str):
                absolute = urljoin(base_url, value)
                if absolute.startswith("http"):
                    urls.add(absolute)
            else:
                urls.update(_iter_jsonld_urls(value, base_url))
    elif isinstance(node, list):
        for item in node:
            urls.update(_iter_jsonld_urls(item, base_url))
    return urls


def _extract_jsonld_urls(html: str, base_url: str) -> set[str]:
    urls: set[str] = set()
    for match in _JSONLD_SCRIPT_RE.finditer(html):
        payload_text = unescape(match.group(1)).strip()
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        urls.update(_iter_jsonld_urls(payload, base_url))
    return urls


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


def filter_visible_links(html: str, links: list[str]) -> list[str]:
    """Remove links wrapped in hidden containers (HTTP-tier honeypot filter)."""
    hidden_hrefs = set()

    a_tag_pattern = re.compile(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
    for match in a_tag_pattern.finditer(html):
        a_tag = match.group(0).lower().replace(" ", "")
        href = match.group(1)
        if (
            "display:none" in a_tag
            or "visibility:hidden" in a_tag
            or "opacity:0" in a_tag
            or "aria-hidden='true'" in a_tag
            or 'aria-hidden="true"' in a_tag
            or "left:-9999px" in a_tag
        ):
            hidden_hrefs.add(href)

    div_pattern = re.compile(
        r'<(?:div|span|section)\s+[^>]*?style=["\'][^"\']*?(?:display:\s*none|visibility:\s*hidden|opacity:\s*0|left:\s*-9999px)[^"\']*?["\'][^>]*>(.*?)</(?:div|span|section)>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in div_pattern.finditer(html):
        hidden_content = match.group(1)
        for a_match in a_tag_pattern.finditer(hidden_content):
            hidden_hrefs.add(a_match.group(1))

    aria_pattern = re.compile(
        r'<(?:div|span|section)\s+[^>]*?aria-hidden=["\']true["\'][^>]*>(.*?)</(?:div|span|section)>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in aria_pattern.finditer(html):
        hidden_content = match.group(1)
        for a_match in a_tag_pattern.finditer(hidden_content):
            hidden_hrefs.add(a_match.group(1))

    visible_links = []
    for link in links:
        is_hidden = False
        for h_href in hidden_hrefs:
            if link.endswith(h_href) or h_href in link:
                is_hidden = True
                break
        if not is_hidden:
            visible_links.append(link)

    return visible_links


def _extract_links_static(
    html: str, base_url: str, url_matcher: re.Pattern[str] | None = None
) -> set[str]:
    """Extract links from static HTML using keyword or regex matching."""
    urls = _extract_all_links(html, base_url)
    urls.update(_extract_jsonld_urls(html, base_url))
    if url_matcher:
        urls.update(_extract_regex_urls(html, base_url, url_matcher))

    urls = set(filter_visible_links(html, list(urls)))
    filtered: set[str] = set()

    for url in urls:
        if url_matcher:
            if url_matcher.search(url):
                filtered.add(url)
        else:
            if is_probable_job_url(url, board_url=base_url):
                filtered.add(url)

    return filtered


def extract_static_job_links(
    html: str,
    base_url: str,
    *,
    url_filter: Any = None,
    limit: int = 3,
) -> list[str]:
    """Return a bounded sample of static job links for non-ingest probes."""
    matcher = _build_url_matcher(url_filter)
    urls = _extract_links_static(html, base_url, matcher)
    normalized_board = base_url.rstrip("/")
    filtered = [
        url
        for url in rank_job_urls(urls, board_url=base_url)
        if url.rstrip("/") != normalized_board
    ]
    return filtered[:limit]


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

    from job_ftch.infrastructure.sources.monitors.shared import (
        BoardGoneError,
        check_ats_redirect,
        is_soft_404,
        raise_if_browser_challenge,
    )

    html = await page.content()
    if is_soft_404(html):
        raise BoardGoneError("Soft 404 detected in browser", url=board_url)
    raise_if_browser_challenge(html, url=board_url)
    check_ats_redirect(html, board_url)

    js_extract = """
    () => {
        let links = [];
        function isVisible(el) {
            if (!el || !el.getBoundingClientRect) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none') return false;
            if (style.visibility === 'hidden') return false;
            if (parseFloat(style.opacity) === 0) return false;
            if (el.getAttribute('aria-hidden') === 'true') return false;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return false;
            if (rect.right < 0 || rect.bottom < 0) return false;
            if (rect.left > window.innerWidth || rect.top > window.innerHeight * 3) return false;
            return true;
        }
        document.querySelectorAll('a').forEach(a => {
            if (a.href && isVisible(a)) links.push(a.href);
        });
        document.querySelectorAll('[role="link"], [data-automation-id="job-title"], [class*="job-card"], [class*="job_card"]').forEach(el => {
            let href = el.getAttribute('href') || el.getAttribute('data-href');
            if (href && isVisible(el)) {
                links.push(new URL(href, window.location.href).href);
            }
        });
        return links.filter(href => href.startsWith('http'));
    }
    """
    urls: list[str] = await page.evaluate(js_extract)

    filtered: set[str] = set()
    for url in urls:
        if url_matcher:
            if url_matcher.search(url):
                filtered.add(url)
        else:
            if is_probable_job_url(url, board_url=board_url):
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


def _auto_listing_candidates(urls: set[str], board_url: str) -> set[str]:
    return {
        url
        for url in urls
        if (
            looks_like_listing_url(url, board_url=board_url)
            # A plural listing route can receive a small positive URL score
            # merely because its path contains a vacancy keyword.  It still
            # needs expansion when no strong detail locator was found.
            or bool(
                re.search(
                    r"/(?:vacancies|openings|careers|вакансии)(?:/|$)",
                    urlparse(url).path,
                    re.IGNORECASE,
                )
            )
        )
        and urlparse(url).netloc == urlparse(board_url).netloc
    }


async def discover(
    spec: Any, client: httpx.AsyncClient, auth: Any = None
) -> set[str] | MonitorResult:
    board_url = spec.url
    config = spec.monitor_config
    render = config.get("render", False)
    bypass_strategy = config.get("_bypass_strategy")
    url_matcher = _build_url_matcher(config.get("url_filter"))
    pagination = config.get("pagination")
    expand_links = config.get("expand_links")
    all_urls: set[str] = set()

    if render:
        try:
            from job_ftch.infrastructure.sources.browser_utils import (
                BROWSER_KEYS,
                install_challenge_response_detector,
                open_page,
            )
        except ImportError as exc:
            raise RuntimeError(
                "patchright is required for DOM monitor with render=true. "
                "Install: pip install patchright && patchright install chromium"
            ) from exc

        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
        browser_config["_pipeline_stats"] = config.get("_pipeline_stats")
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await install_challenge_response_detector(
                page,
                url=board_url,
                controller=bypass_strategy,
                surface="monitor",
            )
            urls = await _extract_links_rendered(page, board_url, config, url_matcher)
    else:
        html = await fetch_page_text(board_url, client)
        if not html:
            log.warning("dom.fetch_failed", board_url=board_url)
            return set()
        from job_ftch.infrastructure.sources.monitors.shared import raise_if_browser_challenge

        raise_if_browser_challenge(html, url=board_url)
        if _has_confirmed_empty_board_message(html):
            log.info("dom.confirmed_empty_board", board_url=board_url)
            return MonitorResult(metadata_updates={"confirmed_empty": True})
        if _has_board_gone_message(html):
            log.info("dom.board_gone", board_url=board_url)
            return MonitorResult(metadata_updates={"board_gone": True})
        all_urls = _extract_all_links(html, board_url)
        urls = _extract_links_static(html, board_url, url_matcher)
        if not expand_links:
            listing_urls = _auto_listing_candidates(all_urls, board_url)
            has_strong_detail = any(score_job_url(url, board_url=board_url) >= 8 for url in urls)
            if listing_urls and not has_strong_detail:
                # A page can contain a weak job-looking URL (or a category
                # link that scored just above zero) while still requiring one
                # listing hop to reach actual postings.
                selected_listings = set(
                    rank_job_urls(listing_urls, board_url=board_url)[:MAX_AUTO_EXPANDED_LISTINGS]
                )
                urls.update(await _expand_listing_urls(selected_listings, client, url_matcher))

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
        config.get("include_if_detail_page", True) and _looks_like_detail_page(board_url)
    ):
        urls.add(board_url)

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(rank_job_urls(urls, board_url=board_url)[:MAX_URLS])

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


register_monitor(
    "dom",
    discover,
    cost=50,
    rich=False,
    can_handle=can_handle,
    scraper_chain=("json-ld", "dom", "embedded", "maintext"),
)
