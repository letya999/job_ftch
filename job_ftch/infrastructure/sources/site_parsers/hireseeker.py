"""Dedicated parser for HireSeeker's category listings and vacancy details."""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain.site_models import DiscoveredPostingPayload, ScrapedPostingPayload
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.scrapers.json_ld import parse_html
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    is_challenge_response,
    keywords_from_spec,
    normalize_search_keywords,
    resolve_browser_config,
    text_matches_keywords,
    with_query_params,
)
from job_ftch.infrastructure.sources.site_utils import payload_to_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_DOMAIN_PATTERN = r"^https?://(?:www\.)?hireseeker\.ru(?:/|$)"
_URL_FILTER = r"hireseeker\.ru/vacancy/\d+(?:-[a-z0-9_-]+)?/?$"
_DETAIL_URL_RE = re.compile(
    r"^https?://(?:www\.)?hireseeker\.ru/vacancy/(\d+)(?:-[^/?#\s]+)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_LISTING_PATH_RE = re.compile(r"^/vacancy-list(?:/[a-z0-9_-]+)?/?$", re.IGNORECASE)
_CATEGORY_PATH_RE = re.compile(r"^/vacancy-list/[a-z0-9_-]+/?$", re.IGNORECASE)


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _is_detail_url(url: str) -> bool:
    return bool(_DETAIL_URL_RE.fullmatch(_canonical_url(url)))


def _detail_identity(url: str) -> str:
    match = _DETAIL_URL_RE.fullmatch(url)
    if match:
        return match.group(1)
    return _canonical_url(url).casefold()


def _jsonld_documents(html_text: str) -> list[Any]:
    documents: list[Any] = []
    for script in LexborHTMLParser(html_text).css('script[type="application/ld+json"]'):
        raw = script.text().strip()
        if not raw:
            continue
        try:
            documents.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            try:
                documents.append(json.loads(html.unescape(raw)))
            except (TypeError, json.JSONDecodeError):
                continue
    return documents


def _jsonld_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(_jsonld_objects(item))
        return result
    if not isinstance(value, dict):
        return []
    result = [value]
    if value.get("@graph") is not None:
        result.extend(_jsonld_objects(value["@graph"]))
    return result


def _is_type(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    return isinstance(value, list) and expected in value


def _job_posting(html_text: str) -> dict[str, Any] | None:
    for document in _jsonld_documents(html_text):
        for item in _jsonld_objects(document):
            if _is_type(item.get("@type"), "JobPosting"):
                return item
    return None


def _listing_cards(html_text: str, base_url: str) -> dict[str, dict[str, str]]:
    tree = LexborHTMLParser(html.unescape(html_text))
    cards: dict[str, dict[str, str]] = {}

    def add_card(node: Any, anchor: Any | None = None) -> None:
        anchor = anchor or node.css_first('a[data-testid="vacancy-title-link"]')
        if anchor is None:
            for candidate in node.css("a[href]"):
                if _is_detail_url(urljoin(base_url, str(candidate.attributes.get("href", "")))):
                    anchor = candidate
                    break
        if anchor is None:
            return
        url = _canonical_url(
            urljoin(base_url, html.unescape(str(anchor.attributes.get("href", "") or "")))
        )
        if not _is_detail_url(url):
            return
        identity = _detail_identity(url)
        title = " ".join(anchor.text(separator=" ", strip=True).split())
        card_text = " ".join(node.text(separator=" ", strip=True).split())
        cards.setdefault(identity, {"url": url, "title": title, "text": card_text})

    for node in tree.css("[data-vacancy-card]"):
        add_card(node)
    if not cards:
        for anchor in tree.css('a[data-testid="vacancy-title-link"]'):
            add_card(anchor.parent or anchor, anchor)

    # A future/alternate rendering may expose an ItemList instead of the card
    # data attribute. Keep the same URL-and-ID contract for that surface.
    for document in _jsonld_documents(html_text):
        for listing in _jsonld_objects(document):
            elements = listing.get("itemListElement")
            if not isinstance(elements, list):
                continue
            for element in elements:
                item = element.get("item") if isinstance(element, dict) else None
                if not isinstance(item, dict):
                    item = element if isinstance(element, dict) else {}
                raw_url = item.get("url")
                if not isinstance(raw_url, str):
                    continue
                url = _canonical_url(urljoin(base_url, html.unescape(raw_url)))
                if _is_detail_url(url):
                    identity = _detail_identity(url)
                    cards.setdefault(
                        identity,
                        {
                            "url": url,
                            "title": str(item.get("name") or item.get("title") or "").strip(),
                            "text": str(item.get("description") or ""),
                        },
                    )
    return cards


def _seed_listing_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        not _LISTING_PATH_RE.fullmatch(parsed.path or "/")
        or parsed.path.rstrip("/") == "/vacancy-list"
    ):
        parsed = parsed._replace(path="/vacancy-list/backend")
    return urlunparse(parsed)


def _category_urls(html_text: str, base_url: str, seed_url: str) -> list[str]:
    seed_query = dict(parse_qsl(urlparse(seed_url).query, keep_blank_values=True))
    result: list[str] = []
    seen: set[str] = set()
    tree = LexborHTMLParser(html.unescape(html_text))
    for anchor in tree.css("a[href]"):
        candidate = urljoin(base_url, html.unescape(str(anchor.attributes.get("href", "") or "")))
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if host != "hireseeker.ru" or not _CATEGORY_PATH_RE.fullmatch(parsed.path):
            continue
        query = {**seed_query, **dict(parse_qsl(parsed.query, keep_blank_values=True))}
        query.pop("page", None)
        normalized = urlunparse(parsed._replace(query=urlencode(query)))
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _page_number(url: str) -> int:
    value = dict(parse_qsl(urlparse(url).query, keep_blank_values=True)).get("page")
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _next_listing_url(html_text: str, base_url: str) -> str | None:
    current_page = _page_number(base_url)
    listing_path = urlparse(base_url).path.rstrip("/") or "/"
    candidates: list[tuple[int, str]] = []
    tree = LexborHTMLParser(html.unescape(html_text))
    for node in tree.css(
        'link[rel="next"], a[rel="next"], a[data-vacancy-list-pagination], a[href]'
    ):
        href = str(node.attributes.get("href", "") or "").strip()
        if not href:
            continue
        candidate = urljoin(base_url, html.unescape(href))
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if host != "hireseeker.ru" or parsed.path.rstrip("/") != listing_path:
            continue
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        try:
            page = int(query.get("page", ""))
        except ValueError:
            continue
        if page > current_page:
            candidates.append((page, candidate))
    return min(candidates)[1] if candidates else None


def _dom_text(tree: LexborHTMLParser, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = tree.css_first(selector)
        if node is not None:
            value = " ".join(node.text(separator=" ", strip=True).split())
            if value:
                return value
    return ""


def _dom_detail(html_text: str) -> tuple[str, str]:
    tree = LexborHTMLParser(html.unescape(html_text))
    title = _dom_text(tree, ("h1",))
    description = _dom_text(
        tree,
        (
            '[data-testid="vacancy-description"]',
            "section",
            "div.prose",
        ),
    )
    return title, description


def _schema_locations(posting: dict[str, Any] | None) -> list[str]:
    if posting is None:
        return []
    result: list[str] = []
    raw_locations = posting.get("jobLocation")
    locations = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        values = []
        for field in ("addressLocality", "addressRegion", "addressCountry"):
            value = address.get(field)
            if isinstance(value, dict):
                value = value.get("name")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        if values:
            result.append(", ".join(values))
    applicant = posting.get("applicantLocationRequirements")
    applicants = applicant if isinstance(applicant, list) else [applicant]
    for value in applicants:
        if isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"].strip():
            result.append(value["name"].strip())
    return list(dict.fromkeys(result))


def _display_locations(posting: dict[str, Any] | None) -> list[str] | None:
    if posting is None:
        return None
    result: list[str] = []
    applicant = posting.get("applicantLocationRequirements")
    applicants = applicant if isinstance(applicant, list) else [applicant]
    for value in applicants:
        if isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"].strip():
            result.append(value["name"].strip())
    raw_locations = posting.get("jobLocation")
    locations = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        values = [
            str(address[field]).strip()
            for field in ("addressLocality", "addressRegion")
            if isinstance(address.get(field), str) and address[field].strip()
        ]
        if values:
            result.append(", ".join(values))
    return list(dict.fromkeys(result)) or None


def _detail_payload(url: str, html_text: str) -> DiscoveredPostingPayload | None:
    if is_challenge_response(html_text):
        return None
    posting = _job_posting(html_text)
    scraped = parse_html(html_text, url=url)
    dom_title, dom_description = _dom_detail(html_text)
    title = str((posting or {}).get("title") or (posting or {}).get("name") or "").strip()
    description = (posting or {}).get("description")
    if not isinstance(description, str) or not description.strip():
        description = dom_description
    title = title or dom_title
    if not title or not description:
        return None

    if scraped is None:
        scraped = ScrapedPostingPayload(title=title, description=description)
    else:
        scraped.title = title
        scraped.description = description
    locations = _display_locations(posting)
    if locations is not None:
        scraped.locations = locations
    identifier = (posting or {}).get("identifier")
    source_name = identifier.get("name") if isinstance(identifier, dict) else None
    vacancy_id = identifier.get("value") if isinstance(identifier, dict) else _detail_identity(url)
    metadata = {
        **(scraped.metadata or {}),
        "parser": "hireseeker",
        "board_url": urlparse(url).scheme + "://" + (urlparse(url).netloc or "hireseeker.ru"),
        "job_url": url,
        "hireseeker_id": str(vacancy_id or _detail_identity(url)),
        "detail_vacancy_confirmed": True,
        "source_locations_schema": _schema_locations(posting),
        "country_authoritative": False,
    }
    if source_name:
        metadata["source_platform"] = str(source_name)
    payload_data = asdict(scraped)
    payload_data["metadata"] = metadata
    return DiscoveredPostingPayload(url=url, **payload_data)


def _challenge_error(url: str, response: Any) -> Exception:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    if isinstance(response, str):
        body = response
        status = None
        headers: dict[str, str] = {}
    else:
        body = str(getattr(response, "text", "") or "")
        status = getattr(response, "status_code", None)
        headers = dict(getattr(response, "headers", {}) or {})
    return BrowserChallengeError(
        url=url,
        status_code=status,
        headers=headers,
        body=(getattr(response, "content", b"") if not isinstance(response, str) else b"")
        or body.encode(),
        challenge_type="captcha",
    )


def _status_from_exception(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _protected_status(status: int | None) -> bool:
    return status in {401, 403, 429, 503}


def _is_challenge_error(exc: BaseException) -> bool:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    return isinstance(exc, BrowserChallengeError)


def _listing_payload(
    url: str, card: dict[str, str], spec: CareerSiteSpec
) -> DiscoveredPostingPayload | None:
    title = str(card.get("title") or "").strip()
    if not title:
        return None
    return DiscoveredPostingPayload(
        url=url,
        title=title,
        metadata={
            "parser": "hireseeker_card",
            "board_url": spec.url,
            "job_url": url,
            "hireseeker_id": _detail_identity(url),
            "detail_vacancy_confirmed": False,
        },
    )


class HireSeekerParser:
    """Parse HireSeeker's category pages and locally filter card content."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    terminal_on_empty = True
    supports_search = True
    search_mode = "per_keyword"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            include_if_detail_page=True,
            extra={
                "listing_page_size": 25,
                "max_listing_pages": 30,
                "max_listing_categories": 20,
                "detail_concurrency": 4,
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 30,
                },
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _max_pages(self, spec: CareerSiteSpec, limit: int) -> int:
        monitor = spec.monitor_config
        try:
            configured = int(monitor.get("max_listing_pages", 30))
            page_size = max(1, int(monitor.get("listing_page_size", 25)))
        except (TypeError, ValueError):
            configured, page_size = 30, 25
        return max(1, min(100, max(configured, (limit + page_size - 1) // page_size)))

    def _max_categories(self, spec: CareerSiteSpec) -> int:
        try:
            return max(1, min(50, int(spec.monitor_config.get("max_listing_categories", 20))))
        except (TypeError, ValueError):
            return 20

    def _detail_concurrency(self, spec: CareerSiteSpec) -> int:
        configured = spec.monitor_config.get("detail_concurrency")
        if configured is None:
            from job_ftch.config import get_settings

            configured = get_settings().career_site_detail_concurrency
        try:
            return max(1, min(50, int(configured)))
        except (TypeError, ValueError):
            return 4

    def _browser_config(self, spec: CareerSiteSpec, bypass_strategy: Any) -> dict[str, Any]:
        config = resolve_browser_config(
            spec,
            bypass_strategy,
            {"headless": True, "stealth": False, "wait": "domcontentloaded"},
        )
        config["_bypass_strategy"] = bypass_strategy
        return config

    async def _browser_search_box(self, page: Any, keywords: list[str], timeout_ms: int) -> bool:
        if not keywords:
            return False
        for selector in (
            'input[name="search"]',
            'input[type="search"]',
            'input[placeholder*="Поиск"]',
        ):
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.fill(" ".join(keywords))
                await locator.press("Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                return True
            except Exception as exc:  # noqa: BLE001 - try the next site control
                logger.debug(
                    "hireseeker.browser_search_box_failed", selector=selector, error=str(exc)
                )
        return False

    async def _discover_with_browser(
        self, spec: CareerSiteSpec, limit: int, bypass_strategy: Any
    ) -> tuple[list[str], dict[str, dict[str, str]]]:
        config = self._browser_config(spec, bypass_strategy)
        keywords = keywords_from_spec(spec)
        current_url = _seed_listing_url(spec.url)
        category_queue = [current_url]
        visited_categories: set[str] = set()
        visited_pages: set[str] = set()
        collected: list[str] = []
        seen: set[str] = set()
        cards: dict[str, dict[str, str]] = {}
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        scroll_loops = int(getattr(browser, "scroll_loops", None) or 12)
        pause_sec = (getattr(browser, "scroll_pause_ms", None) or 500) / 1000
        scroll_px = int(getattr(browser, "scroll_px", None) or 2500)
        stale_rounds = int(getattr(browser, "stale_rounds", None) or 3)
        async with open_page(config, bypass_strategy=bypass_strategy) as page:
            while category_queue and len(collected) < limit:
                category_url = category_queue.pop(0)
                if category_url in visited_categories:
                    continue
                visited_categories.add(category_url)
                page_url = category_url
                for _ in range(self._max_pages(spec, limit)):
                    if page_url in visited_pages:
                        break
                    visited_pages.add(page_url)
                    await navigate(page, page_url, config)
                    rendered_url = urljoin(
                        page_url, str(getattr(page, "url", page_url) or page_url)
                    )
                    content = await page.content()
                    if is_challenge_response(content):
                        raise _challenge_error(rendered_url, content)
                    if (
                        keywords
                        and not _listing_cards(content, rendered_url)
                        and await self._browser_search_box(
                            page, keywords, int(config.get("timeout", 30_000))
                        )
                    ):
                        content = await page.content()
                        rendered_url = urljoin(
                            rendered_url,
                            str(getattr(page, "url", rendered_url) or rendered_url),
                        )
                    urls = await browser_scroll_collect_urls(
                        page,
                        rendered_url,
                        _DETAIL_URL_RE,
                        limit=limit,
                        scroll_loops=scroll_loops,
                        pause_sec=pause_sec,
                        scroll_px=scroll_px,
                        stale_rounds=stale_rounds,
                    )
                    content = await page.content()
                    page_cards = _listing_cards(content, rendered_url)
                    cards.update(page_cards)
                    for category in _category_urls(content, rendered_url, spec.url):
                        if category not in visited_categories and category not in category_queue:
                            category_queue.append(category)
                    for raw_url in urls:
                        url = _canonical_url(raw_url)
                        identity = _detail_identity(url)
                        card = page_cards.get(identity, {})
                        if keywords and not text_matches_keywords(card.get("text", ""), keywords):
                            continue
                        if identity in seen or not _is_detail_url(url):
                            continue
                        seen.add(identity)
                        collected.append(url)
                        if len(collected) >= limit:
                            break
                    if len(collected) >= limit:
                        break
                    next_url = _next_listing_url(content, rendered_url)
                    if next_url is None and urls:
                        next_url = _page_url(rendered_url, _page_number(rendered_url) + 1)
                    if not next_url:
                        break
                    page_url = next_url
        return collected[:limit], cards

    async def _browser_detail(
        self, spec: CareerSiteSpec, url: str, bypass_strategy: Any
    ) -> DiscoveredPostingPayload | None:
        config = self._browser_config(spec, bypass_strategy)
        async with open_page(config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, url, config)
            final_url = _canonical_url(urljoin(url, str(getattr(page, "url", url) or url)))
            content = await page.content()
            if is_challenge_response(content):
                raise _challenge_error(final_url, content)
            if not _is_detail_url(final_url) or _detail_identity(final_url) != _detail_identity(
                url
            ):
                return None
            return _detail_payload(final_url, content)

    async def _detail(
        self,
        spec: CareerSiteSpec,
        url: str,
        card: dict[str, str],
        client: Any,
        bypass_strategy: Any,
    ) -> DiscoveredPostingPayload | None:
        response: Any | None = None
        try:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - card fallback covers ordinary detail loss
            if _protected_status(_status_from_exception(exc)):
                raise
            if bypass_strategy is None:
                return _listing_payload(url, card, spec)

        if response is not None:
            final_url = _canonical_url(str(getattr(response, "url", url) or url))
            if not _is_detail_url(final_url) or _detail_identity(final_url) != _detail_identity(
                url
            ):
                return None
            body = str(getattr(response, "text", "") or "")
            if is_challenge_response(body):
                if bypass_strategy is None:
                    raise _challenge_error(final_url, response)
                response = None
            else:
                payload = _detail_payload(final_url, body)
                if payload is not None:
                    payload.metadata = {
                        **(payload.metadata or {}),
                        "board_url": spec.url,
                    }
                    return payload

        if bypass_strategy is not None:
            payload = await self._browser_detail(spec, url, bypass_strategy)
            if payload is not None:
                payload.metadata = {
                    **(payload.metadata or {}),
                    "board_url": spec.url,
                }
                return payload
        return _listing_payload(url, card, spec)

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = max(1, int(spec.limit or 50))
        source_name = spec.source_name or "hireseeker"
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        category_queue = [_seed_listing_url(spec.url)]
        visited_categories: set[str] = set()
        visited_pages: set[str] = set()
        collected: list[str] = []
        cards: dict[str, dict[str, str]] = {}
        seen: set[str] = set()
        listing_error: Exception | None = None
        keywords = keywords_from_spec(spec)

        while category_queue and len(collected) < limit:
            category_url = category_queue.pop(0)
            if category_url in visited_categories or len(
                visited_categories
            ) >= self._max_categories(spec):
                continue
            visited_categories.add(category_url)
            page_url = category_url
            for _ in range(self._max_pages(spec, limit)):
                if page_url in visited_pages or len(collected) >= limit:
                    break
                visited_pages.add(page_url)
                try:
                    response = await client.get(page_url, follow_redirects=True)
                    response.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - browser fallback may rescue listings
                    listing_error = exc
                    break
                response_url = str(getattr(response, "url", page_url) or page_url)
                body = str(getattr(response, "text", "") or "")
                if is_challenge_response(body):
                    listing_error = _challenge_error(response_url, response)
                    break
                page_cards = _listing_cards(body, response_url)
                if not page_cards:
                    break
                cards.update(page_cards)
                for category in _category_urls(body, response_url, spec.url):
                    if category not in visited_categories and category not in category_queue:
                        category_queue.append(category)
                for identity, card in page_cards.items():
                    if keywords and not text_matches_keywords(card.get("text", ""), keywords):
                        continue
                    url = _canonical_url(card["url"])
                    if identity in seen or not _is_detail_url(url):
                        continue
                    seen.add(identity)
                    collected.append(url)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
                next_url = _next_listing_url(body, response_url)
                if next_url is None:
                    next_url = _page_url(response_url, _page_number(response_url) + 1)
                page_url = next_url

            if listing_error is not None:
                break

        if listing_error is not None:
            if _protected_status(_status_from_exception(listing_error)):
                raise listing_error
            if _is_challenge_error(listing_error) and bypass_strategy is None:
                raise listing_error
            if bypass_strategy is not None:
                try:
                    browser_urls, browser_cards = await self._discover_with_browser(
                        spec, limit, bypass_strategy
                    )
                    cards.update(browser_cards)
                    for url in browser_urls:
                        identity = _detail_identity(url)
                        if identity not in seen:
                            seen.add(identity)
                            collected.append(url)
                            if len(collected) >= limit:
                                break
                except Exception:
                    if not collected or _is_challenge_error(listing_error):
                        raise
            if not collected:
                raise listing_error

        if not collected:
            return
        stats = spec.monitor_config.get("_pipeline_stats")
        if stats is not None:
            stats.parser_urls_discovered = len(collected)

        detail_limit = spec.detail_limit
        if detail_limit is None:
            from job_ftch.config import get_settings

            detail_limit = get_settings().career_site_default_detail_limit
        detail_urls = collected if detail_limit is None else collected[: max(0, int(detail_limit))]
        listing_only_urls = collected[len(detail_urls) :]
        semaphore = asyncio.Semaphore(self._detail_concurrency(spec))

        async def bounded(url: str) -> DiscoveredPostingPayload | None:
            async with semaphore:
                return await self._detail(
                    spec, url, cards.get(_detail_identity(url), {}), client, bypass_strategy
                )

        tasks = [asyncio.create_task(bounded(url)) for url in detail_urls]
        try:
            for task in asyncio.as_completed(tasks):
                payload = await task
                if payload is not None:
                    yield payload_to_raw_item(payload, spec, source_name)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        for url in listing_only_urls:
            payload = _listing_payload(url, cards.get(_detail_identity(url), {}), spec)
            if payload is not None:
                yield payload_to_raw_item(payload, spec, source_name)

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        listing_url = _seed_listing_url(base_url)
        return [with_query_params(listing_url, {"search": term}) for term in terms]

    @property
    def __name__(self) -> str:
        return "HireSeekerParser"


register_site_parser(
    "hireseeker",
    domain_pattern=HireSeekerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hireseeker.ru",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="HireSeeker exposes category SSR cards and internal JobPosting detail pages; free-text search is applied locally.",
    ),
)(HireSeekerParser)
