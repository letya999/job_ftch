"""Shared helpers for discover-oriented site parsers."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
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
    from collections.abc import Callable, Sequence

    import httpx

    from job_ftch.config import Settings
    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DISTINCTIVE_TOKEN = re.compile(
    r"^(?:ai|llm|ml|mlops|genai|agent|agentic|нейро\w*|машинн\w*)$",
    re.IGNORECASE,
)
_OR_SPLIT = re.compile(r"\s+OR\s+|\s+or\s+")
_PAGE_PARAM_NAMES: tuple[str, ...] = (
    "page",
    "p",
    "pg",
    "pageNumber",
    "page_number",
    "pageNo",
    "pagenum",
)
_OFFSET_PARAM_NAMES: tuple[str, ...] = ("offset", "skip", "start", "from", "startIndex")
_CURSOR_PARAM_NAMES: tuple[str, ...] = (
    "cursor",
    "after",
    "next",
    "next_cursor",
    "pageToken",
    "page_token",
)
_NEXT_JSON_KEYS: tuple[str, ...] = (
    "next",
    "next_page",
    "nextPage",
    "next_url",
    "nextUrl",
    "next_cursor",
    "nextCursor",
    "cursor",
)
DEFAULT_LISTING_MAX_PAGES = 5


@dataclass(frozen=True)
class ListingPagination:
    """How to walk a listing after the first page.

    ``start`` is the page/offset of the first listing page. ``paginate_listing``
    still fetches ``start_url`` as index 0 and only rewrites the query for
    later pages, so SuperJob ``?keywords=`` is preserved while ``page=2`` is
    appended.
    """

    kind: Literal["page", "offset", "cursor", "next_url"] = "page"
    param: str = "page"
    start: int = 1
    page_size: int = 20
    max_pages: int = DEFAULT_LISTING_MAX_PAGES


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
    "job_search",
    "roles",
    "s",
)


def keywords_from_spec(spec: Any) -> list[str]:
    """Read compacted search terms from the URL, then ``_search_keywords``.

    Parser search URLs carry ``query=A OR B``. Expansion also stores the same
    terms on ``monitor_config["_search_keywords"]`` for boards that filter
    locally instead of having a server-side search API.
    """
    url = str(getattr(spec, "url", "") or "")
    query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    for key in SEARCH_QUERY_PARAM_NAMES:
        value = query.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_search_keywords(_OR_SPLIT.split(value))
    monitor = getattr(spec, "monitor_config", None) or {}
    if isinstance(monitor, dict):
        attached = monitor.get("_search_keywords")
        if attached:
            return normalize_search_keywords(attached)
    return normalize_search_keywords(getattr(spec, "target_roles", None) or ())


def text_matches_keywords(text: str, keywords: Sequence[str] | None) -> bool:
    """True when ``text`` matches any search term, or when no terms are set.

    Empty keywords mean "do not filter". A term matches as a phrase, as all of
    its tokens, or via a distinctive AI/LLM/ML token so ``ML Engineer`` still
    hits a listing titled ``ML-разработчик``.
    """
    terms = [term for term in (keywords or ()) if isinstance(term, str) and term.strip()]
    if not terms:
        return True
    haystack = str(text or "").casefold()
    if not haystack.strip():
        return True
    for term in terms:
        phrase = " ".join(term.casefold().split())
        if not phrase or phrase in {"or"}:
            continue
        if phrase in haystack:
            return True
        tokens = [token for token in re.findall(r"[a-z0-9а-яё]+", phrase) if token != "or"]
        if tokens and all(token in haystack for token in tokens):
            return True
        distinctive = [token for token in tokens if _DISTINCTIVE_TOKEN.fullmatch(token)]
        if distinctive and any(
            re.search(rf"(?<![a-z0-9а-яё]){re.escape(token)}(?![a-z0-9а-яё])", haystack)
            for token in distinctive
        ):
            return True
    return False


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


def distinctive_search_tokens(keywords: Any, *, cap: int = 6) -> list[str]:
    """Keep only AI/LLM/ML tokens so SuperJob/Ozon do not match every Engineer.

    Live SuperJob ``keywords=LLM Engineer OR ML Engineer`` returns QA Engineer
    cards because the board tokenizes ``Engineer``. Ozon ``query=ML Engineer``
    and ``query=LLM OR ML`` both return zero; a single distinctive token works.
    """
    seen: set[str] = set()
    result: list[str] = []
    for term in normalize_search_keywords(keywords):
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", term):
            folded = token.casefold()
            if folded in seen or not _DISTINCTIVE_TOKEN.fullmatch(folded):
                continue
            seen.add(folded)
            result.append(token)
            if len(result) >= cap:
                return result
    return result


def with_query_params(url: str, params: dict[str, str]) -> str:
    """Return ``url`` with ``params`` merged into its query string (overwrite)."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def listing_page_url(
    url: str,
    pagination: ListingPagination,
    *,
    index: int,
    cursor: str | None = None,
) -> str:
    """URL for listing page ``index`` (0 = the original ``url``)."""
    if index <= 0 and pagination.kind != "next_url":
        return url
    if pagination.kind == "next_url":
        return cursor or url
    if pagination.kind == "cursor":
        if not cursor:
            return url
        return with_query_params(url, {pagination.param: cursor})
    if pagination.kind == "offset":
        value = pagination.start + index * pagination.page_size
        return with_query_params(url, {pagination.param: str(value)})
    page_number = pagination.start + index
    if page_number <= 1:
        return url
    return with_query_params(url, {pagination.param: str(page_number)})


def _json_next_value(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in _NEXT_JSON_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("pagination", "meta", "page", "links"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in (*_NEXT_JSON_KEYS, "nextPageUrl", "next_page_url"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def extract_next_listing_url(html: str, url: str, payload: Any = None) -> str | None:
    """Absolute next-page URL from ``rel=next``, JSON next, or a page=N+1 link."""
    next_value = _json_next_value(payload)
    if next_value:
        if next_value.startswith("http") or next_value.startswith("/"):
            return urljoin(url, next_value)
        return next_value
    tree = HTMLParser(html or "")
    for node in tree.css('link[rel="next"], a[rel="next"]'):
        href = str(node.attributes.get("href") or "").strip()
        if href:
            return urljoin(url, href)
    parsed = urlparse(url)
    current = dict(parse_qsl(parsed.query, keep_blank_values=True))
    current_page = None
    for key in _PAGE_PARAM_NAMES:
        raw = current.get(key)
        if raw and raw.isdigit():
            current_page = int(raw)
            break
    if current_page is None:
        current_page = 1
    host = (parsed.hostname or "").lower()
    for anchor in tree.css("a[href]"):
        href = str(anchor.attributes.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(url, href)
        candidate = urlparse(absolute)
        if host and (candidate.hostname or "").lower() not in {
            host,
            f"www.{host}",
            host.removeprefix("www."),
        }:
            continue
        query = dict(parse_qsl(candidate.query, keep_blank_values=True))
        for key in _PAGE_PARAM_NAMES:
            raw = query.get(key)
            if raw and raw.isdigit() and int(raw) == current_page + 1:
                return absolute.split("#", 1)[0]
    return None


def detect_listing_pagination(
    html: str,
    url: str,
    payload: Any = None,
) -> ListingPagination | None:
    """Infer page/offset/cursor/next-link pagination from HTML or a JSON payload."""
    next_value = _json_next_value(payload)
    if next_value:
        if next_value.startswith("http") or next_value.startswith("/"):
            return ListingPagination(kind="next_url")
        param = "cursor"
        if isinstance(payload, dict):
            for key in _CURSOR_PARAM_NAMES:
                if payload.get(key) == next_value:
                    param = key
                    break
        return ListingPagination(kind="cursor", param=param)

    query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    for key in _OFFSET_PARAM_NAMES:
        if key in query:
            raw = query.get(key) or "0"
            start = int(raw) if raw.isdigit() else 0
            size = 20
            for size_key in ("limit", "take", "pageSize", "page_size", "per_page"):
                size_raw = query.get(size_key)
                if size_raw and size_raw.isdigit():
                    size = max(1, int(size_raw))
                    break
            return ListingPagination(kind="offset", param=key, start=start, page_size=size)
    for key in _PAGE_PARAM_NAMES:
        if key in query:
            raw = query.get(key) or "1"
            start = int(raw) if raw.isdigit() else 1
            return ListingPagination(kind="page", param=key, start=start)
    for key in _CURSOR_PARAM_NAMES:
        if query.get(key):
            return ListingPagination(kind="cursor", param=key)

    tree = HTMLParser(html or "")
    if tree.css_first('link[rel="next"], a[rel="next"]') is not None:
        return ListingPagination(kind="next_url")
    host = (urlparse(url).hostname or "").lower()
    for anchor in tree.css("a[href]"):
        href = str(anchor.attributes.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(url, href)
        candidate = urlparse(absolute)
        if host and (candidate.hostname or "").lower() not in {
            host,
            f"www.{host}",
            host.removeprefix("www."),
        }:
            continue
        found = dict(parse_qsl(candidate.query, keep_blank_values=True))
        for key in _PAGE_PARAM_NAMES:
            raw = found.get(key)
            if raw and raw.isdigit() and int(raw) > 1:
                return ListingPagination(kind="page", param=key, start=1)
        for key in _OFFSET_PARAM_NAMES:
            if found.get(key):
                return ListingPagination(kind="offset", param=key, start=0)
        for key in _CURSOR_PARAM_NAMES:
            if found.get(key):
                return ListingPagination(kind="cursor", param=key)
    return None


def _response_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    text = getattr(raw, "text", None)
    return str(text) if text is not None else ""


def _response_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    loads = getattr(raw, "json", None)
    if callable(loads):
        try:
            return loads()
        except Exception:  # noqa: BLE001 - pagination detection is best-effort
            return None
    return None


async def paginate_listing(
    fetch: Any,
    extract: Any,
    start_url: str,
    *,
    limit: int,
    pagination: ListingPagination | None = None,
    identity: Any = None,
) -> list[Any]:
    """Walk listing pages until ``limit``, an empty page, or a repeated page.

    ``fetch(url)`` returns HTML text or an HTTP response. ``extract(html, url)``
    returns items from one page. Unknown boards default to ``page=2,3,...``.
    """
    collected: list[Any] = []
    seen_keys: set[str] = set()
    seen_bodies: set[str] = set()
    cursor: str | None = None
    resolved = pagination
    max_pages = pagination.max_pages if pagination is not None else DEFAULT_LISTING_MAX_PAGES
    for index in range(max(1, max_pages)):
        if resolved is not None and resolved.kind == "next_url" and index > 0:
            page_url = cursor
            if not page_url:
                break
        else:
            spec = resolved or ListingPagination()
            page_url = listing_page_url(start_url, spec, index=index, cursor=cursor)
        try:
            raw = await fetch(page_url)
        except Exception:  # noqa: BLE001 - extra pages must not fail the listing
            break
        html = _response_text(raw)
        if not html:
            break
        body_key = html[:500]
        if index > 0 and body_key in seen_bodies:
            break
        seen_bodies.add(body_key)
        payload = _response_json(raw)
        if resolved is None:
            resolved = detect_listing_pagination(html, page_url, payload) or ListingPagination()
            max_pages = resolved.max_pages
        items = list(extract(html, page_url) or ())
        new_count = 0
        for item in items:
            key = str(identity(item) if identity is not None else item)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            collected.append(item)
            new_count += 1
            if len(collected) >= limit:
                return collected[:limit]
        if index > 0 and new_count == 0:
            break
        cursor = extract_next_listing_url(html, page_url, payload)
        if resolved.kind in {"cursor", "next_url"} and not cursor:
            break
        if not items and index > 0:
            break
    return collected[:limit]


def pagination_monitor_config(
    pagination: ListingPagination | None,
    *,
    first_page_already_fetched: bool = True,
) -> dict[str, Any] | None:
    """DOM-monitor ``pagination`` dict, or None when there is nothing to walk."""
    if pagination is None:
        return {
            "param_name": "page",
            "start": 2 if first_page_already_fetched else 1,
            "increment": 1,
            "max_pages": DEFAULT_LISTING_MAX_PAGES,
        }
    if pagination.kind == "next_url":
        return {"follow_rel_next": True, "max_pages": pagination.max_pages}
    start = pagination.start
    if pagination.kind == "page" and first_page_already_fetched:
        start = pagination.start + 1
    increment = pagination.page_size if pagination.kind == "offset" else 1
    return {
        "param_name": pagination.param,
        "start": start,
        "increment": increment,
        "max_pages": pagination.max_pages,
    }


def url_has_search_query(url: str, param_names: Any = SEARCH_QUERY_PARAM_NAMES) -> bool:
    """True if ``url`` already carries a non-empty known search parameter.

    Used to identify an existing search surface. Runtime expansion may rebuild
    it from current target roles unless the source sets ``search_locked``.
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
