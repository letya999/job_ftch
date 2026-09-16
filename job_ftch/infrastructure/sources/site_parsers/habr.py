"""Dedicated HTTP/browser parser for career.habr.com vacancies."""

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
    listing_matches_keywords,
    normalize_search_keywords,
    resolve_browser_config,
    safe_fetch,
    with_query_params,
)
from job_ftch.infrastructure.sources.site_utils import payload_to_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_DOMAIN_PATTERN = r"^https?://career\.habr\.com(?:/|$)"
_URL_FILTER = r"career\.habr\.com/vacancies/\d+(?:[^/?#\s]*)?/?$"
_DETAIL_URL_RE = re.compile(
    r"^https?://career\.habr\.com/vacancies/(\d+)(?:[^/?#\s]*)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_DETAIL_LINK_RE = re.compile(
    r"((?:https?://career\.habr\.com)?/vacancies/\d+(?:[^\"' <]*)?)",
    re.IGNORECASE,
)
_LISTING_PATH_RE = re.compile(r"/vacancies(?:/[a-z0-9_-]+)?/?$", re.IGNORECASE)


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _canonical_detail_url(url: str) -> str:
    return _canonical_url(url)


def _detail_identity(url: str) -> str:
    match = _DETAIL_URL_RE.search(url)
    return match.group(1) if match else _canonical_url(url).casefold()


def _is_detail_url(url: str) -> bool:
    return bool(_DETAIL_URL_RE.fullmatch(_canonical_url(url)))


def _as_int(value: object, default: int) -> int:
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return default


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


def _parse_job_posting_jsonld(html_text: str) -> dict[str, Any] | None:
    for document in _jsonld_documents(html_text):
        for item in _jsonld_objects(document):
            if _is_type(item.get("@type"), "JobPosting"):
                return item
    return None


def _listing_cards(html_text: str, base_url: str) -> dict[str, dict[str, str]]:
    tree = LexborHTMLParser(html.unescape(html_text))
    cards: dict[str, dict[str, str]] = {}
    anchors = tree.css("a.vacancy-card__title-link")
    if not anchors:
        anchors = tree.css('a[class*="vacancy-card__title-link"]')
    if not anchors:
        anchors = tree.css("a[href]")

    for anchor in anchors:
        raw_href = str(anchor.attributes.get("href", "") or "").strip()
        url = _canonical_detail_url(urljoin(base_url, html.unescape(raw_href)))
        if not _is_detail_url(url):
            continue
        identity = _detail_identity(url)
        title = " ".join(anchor.text(separator=" ", strip=True).split())
        card: Any = anchor
        while card is not None and "vacancy-card" not in str(card.attributes.get("class") or ""):
            card = card.parent
        card_text = " ".join((card or anchor).text(separator=" ", strip=True).split())
        current = cards.get(identity)
        if current is None:
            cards[identity] = {"url": url, "title": title, "text": card_text}
        else:
            if not current["title"] and title:
                current["title"] = title
            if len(card_text) > len(current["text"]):
                current["text"] = card_text

    # Keep an alternate structured-listing path available if Habr changes its
    # card markup while retaining Schema.org ItemList.
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
                url = _canonical_detail_url(urljoin(base_url, html.unescape(raw_url)))
                if not _is_detail_url(url):
                    continue
                identity = _detail_identity(url)
                current = cards.setdefault(
                    identity,
                    {
                        "url": url,
                        "title": str(item.get("name") or item.get("title") or "").strip(),
                        "text": str(item.get("description") or ""),
                    },
                )
                if not current["title"]:
                    current["title"] = str(item.get("name") or item.get("title") or "").strip()
    return cards


def _normalize_listing_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        return urlunparse(parsed._replace(path="/vacancies"))
    if _is_detail_url(url):
        return urlunparse(parsed._replace(path="/vacancies"))
    if _LISTING_PATH_RE.search(path):
        return urlunparse(parsed)
    return urlunparse(parsed._replace(path="/vacancies"))


def _page_number(url: str) -> int:
    value = dict(parse_qsl(urlparse(url).query, keep_blank_values=True)).get("page")
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _listing_page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _next_listing_url(html_text: str, base_url: str) -> str | None:
    current_page = _page_number(base_url)
    listing_path = urlparse(base_url).path.rstrip("/") or "/"
    tree = LexborHTMLParser(html.unescape(html_text))
    candidates: list[tuple[int, str]] = []
    for node in tree.css('link[rel="next"], a[rel="next"], a[href]'):
        raw_href = str(node.attributes.get("href", "") or "").strip()
        if not raw_href:
            continue
        candidate = urljoin(base_url, html.unescape(raw_href))
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        if host != "career.habr.com":
            continue
        if parsed.path.rstrip("/") != listing_path:
            continue
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        try:
            page = int(query.get("page", ""))
        except (TypeError, ValueError):
            continue
        if page > current_page:
            candidates.append((page, candidate))
    return min(candidates)[1] if candidates else None


def _dom_text(tree: LexborHTMLParser, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = tree.css_first(selector)
        if node is None:
            continue
        value = " ".join(node.text(separator=" ", strip=True).split())
        if value:
            return value
    return ""


def _dom_detail(html_text: str) -> tuple[str, str, str | None, list[str] | None]:
    tree = LexborHTMLParser(html.unescape(html_text))
    title = _dom_text(tree, ("h1",))
    company = (
        _dom_text(
            tree,
            (
                '[class*="company-name"]',
                '[class*="vacancy-company"]',
                'a[href*="/companies/"]',
            ),
        )
        or None
    )
    description = _dom_text(
        tree,
        (
            '[class*="vacancy-description"]',
            '[class*="vacancy__description"]',
            "article",
            "main",
        ),
    )
    location = _dom_text(
        tree,
        ('[class*="vacancy-location"]', '[class*="vacancy__location"]'),
    )
    return title, description, company, [location] if location else None


def _location_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if isinstance(name, str) and name.strip():
        return " ".join(name.split())
    address = value.get("address")
    if isinstance(address, str) and address.strip():
        return " ".join(address.split())
    if not isinstance(address, dict):
        return None
    parts: list[str] = []
    for field in ("addressLocality", "addressRegion"):
        field_value = address.get(field)
        if isinstance(field_value, dict):
            field_value = field_value.get("name")
        if isinstance(field_value, str) and field_value.strip():
            parts.append(" ".join(field_value.split()))
    if parts:
        return ", ".join(parts)
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    if isinstance(country, str) and len(country.strip()) > 3:
        return " ".join(country.split())
    return None


def _posting_locations(posting: dict[str, Any] | None) -> list[str] | None:
    if posting is None:
        return None
    result: list[str] = []
    raw_locations = posting.get("jobLocation")
    locations = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    for location in locations:
        value = _location_text(location)
        if value:
            result.append(value)
    applicant = posting.get("applicantLocationRequirements")
    applicants = applicant if isinstance(applicant, list) else [applicant]
    for requirement in applicants:
        value = _location_text(requirement)
        if value:
            result.append(value)
    return list(dict.fromkeys(result)) or None


def _company_name(posting: dict[str, Any] | None) -> str | None:
    organization = (posting or {}).get("hiringOrganization")
    if isinstance(organization, dict):
        organization = organization.get("name")
    if isinstance(organization, str) and organization.strip():
        return " ".join(organization.split())
    return None


def _detail_payload(
    detail_url: str,
    html_text: str,
    board_url: str,
) -> DiscoveredPostingPayload | None:
    if is_challenge_response(html_text):
        return None
    posting = _parse_job_posting_jsonld(html_text)
    scraped = parse_html(html_text, url=detail_url)
    dom_title, dom_description, dom_company, dom_locations = _dom_detail(html_text)
    posting_title = str((posting or {}).get("title") or (posting or {}).get("name") or "").strip()
    posting_description = (posting or {}).get("description")
    description = (
        posting_description
        if isinstance(posting_description, str) and posting_description.strip()
        else None
    )
    title = posting_title
    if scraped is not None:
        title = title or str(scraped.title or "").strip()
        description = description or scraped.description
    title = title or dom_title
    description = description or dom_description
    if not title or not description:
        return None

    if scraped is None:
        scraped = ScrapedPostingPayload(title=title, description=description)
    else:
        scraped.title = title
        scraped.description = description

    locations = _posting_locations(posting) or scraped.locations or dom_locations
    if locations:
        scraped.locations = locations
    company = (scraped.metadata or {}).get("company") or _company_name(posting) or dom_company
    metadata = {
        **(scraped.metadata or {}),
        "parser": "site_habr_career",
        "board_url": board_url,
        "job_url": detail_url,
        "source_platform": "career.habr.com",
        "detail_vacancy_confirmed": True,
        "country_authoritative": False,
    }
    if locations:
        metadata["source_locations_schema"] = locations
    if company:
        metadata["company"] = company
        metadata["company_authoritative"] = True
    payload_data = asdict(scraped)
    payload_data["metadata"] = metadata
    return DiscoveredPostingPayload(url=detail_url, **payload_data)


def _listing_payload(
    url: str,
    card: dict[str, str],
    spec: CareerSiteSpec,
) -> DiscoveredPostingPayload | None:
    title = str(card.get("title") or "").strip()
    if not title:
        return None
    return DiscoveredPostingPayload(
        url=url,
        title=title,
        metadata={
            "board_url": spec.url,
            "job_url": url,
            "source_platform": "career.habr.com",
            "parser": "site_habr_career_card",
            "listing_text": card.get("text", ""),
            "detail_vacancy_confirmed": False,
        },
    )


def _payload_to_raw_item(
    payload: DiscoveredPostingPayload,
    spec: CareerSiteSpec,
    source_name: str,
) -> RawItem:
    item = payload_to_raw_item(payload, spec, source_name)
    external_id = _detail_identity(payload.url)
    if external_id == payload.url.casefold():
        return item
    data = item.model_dump()
    data["external_id"] = external_id
    return type(item).model_validate(data)


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


def _is_challenge_error(exc: BaseException) -> bool:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    return isinstance(exc, BrowserChallengeError)


class HabrCareerParser:
    """Parse Habr Career's SSR search pages and structured vacancy details."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True
    terminal_on_empty = True

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
        parsed = urlparse(_normalize_listing_url(base_url))
        if len(terms) == 1 and re.fullmatch(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*", terms[0]):
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.pop("q", None)
            query.pop("type", None)
            parsed = parsed._replace(
                path=f"/vacancies/{terms[0].casefold()}",
                query=urlencode(query),
            )
            return [urlunparse(parsed)]
        return [
            with_query_params(
                urlunparse(parsed),
                {"q": " OR ".join(terms), "type": "all"},
            )
        ]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=False,
            include_if_detail_page=True,
            extra={
                "listing_page_size": 25,
                "max_listing_pages": 20,
                "detail_concurrency": 4,
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 20,
                },
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _manifest_value(self, key: str, default: object) -> object:
        manifest_entry = getattr(self, "_manifest_entry", None)
        value = getattr(manifest_entry, key, None) if manifest_entry is not None else None
        return default if value is None else value

    def _extra(self) -> dict[str, Any]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        return extra if isinstance(extra, dict) else {}

    def _extra_value(self, spec: CareerSiteSpec, key: str, default: object) -> object:
        value = spec.monitor_config.get(key)
        if value is not None:
            return value
        return self._extra().get(key, default)

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return int(spec_limit)
        raw_limit = self._manifest_value("limit", 50)
        if isinstance(raw_limit, (int, str, float)):
            return int(raw_limit)
        return 50

    def _max_listing_pages(self, spec: CareerSiteSpec, limit: int) -> int:
        configured = _as_int(self._extra_value(spec, "max_listing_pages", 20), 20)
        page_size = max(1, _as_int(self._extra_value(spec, "listing_page_size", 25), 25))
        return max(1, min(100, max(configured, (limit + page_size - 1) // page_size)))

    def _detail_concurrency(self, spec: CareerSiteSpec) -> int:
        configured = self._extra_value(spec, "detail_concurrency", None)
        if configured is None:
            from job_ftch.config import get_settings

            configured = get_settings().career_site_detail_concurrency
        return max(1, min(50, _as_int(configured, 4)))

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = getattr(manifest_entry, "detail_pattern", None) or _DETAIL_LINK_RE.pattern
        return re.compile(str(pattern), re.IGNORECASE)

    def _browser_config(self, spec: CareerSiteSpec, bypass_strategy: Any) -> dict[str, Any]:
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        config = resolve_browser_config(
            spec,
            bypass_strategy,
            {
                "headless": True,
                "stealth": False,
                "wait": getattr(browser, "wait", None) or "domcontentloaded",
            },
        )
        config["_bypass_strategy"] = bypass_strategy
        return config

    async def _browser_search_box(
        self,
        page: Any,
        keywords: list[str],
        *,
        timeout_ms: int,
    ) -> bool:
        if not keywords:
            return False
        for selector in (
            'input[name="q"]',
            'input[type="search"]',
            'input[placeholder*="Работа"]',
        ):
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.fill(" ".join(keywords))
                await locator.press("Enter")
                wait_for_load_state = getattr(page, "wait_for_load_state", None)
                if callable(wait_for_load_state):
                    await wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                return True
            except Exception as exc:  # noqa: BLE001 - try the next site control
                logger.debug("habr.browser_search_box_failed", selector=selector, error=str(exc))
        return False

    async def _discover_with_browser(
        self,
        spec: CareerSiteSpec,
        *,
        limit: int,
        bypass_strategy: Any,
    ) -> tuple[list[str], dict[str, dict[str, str]]]:
        config = self._browser_config(spec, bypass_strategy)
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        scroll_loops = int(getattr(browser, "scroll_loops", None) or 12)
        pause_sec = (getattr(browser, "scroll_pause_ms", None) or 500) / 1000
        scroll_px = int(getattr(browser, "scroll_px", None) or 2500)
        stale_rounds = int(getattr(browser, "stale_rounds", None) or 3)
        keywords = keywords_from_spec(spec)
        current_url = _normalize_listing_url(spec.url)
        visited: set[str] = set()
        collected: list[str] = []
        seen: set[str] = set()
        cards: dict[str, dict[str, str]] = {}
        async with open_page(
            config,
            use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
            bypass_strategy=bypass_strategy,
        ) as page:
            for _ in range(self._max_listing_pages(spec, limit)):
                if current_url in visited or len(collected) >= limit:
                    break
                visited.add(current_url)
                await navigate(page, current_url, config)
                page_url = urljoin(
                    current_url, str(getattr(page, "url", current_url) or current_url)
                )
                content = await page.content()
                if is_challenge_response(content):
                    raise _challenge_error(page_url, content)
                page_cards = _listing_cards(content, page_url)
                if not page_cards and await self._browser_search_box(
                    page, keywords, timeout_ms=int(config.get("timeout", 30_000))
                ):
                    content = await page.content()
                    page_url = urljoin(page_url, str(getattr(page, "url", page_url) or page_url))
                    if is_challenge_response(content):
                        raise _challenge_error(page_url, content)
                    page_cards = _listing_cards(content, page_url)
                urls = await browser_scroll_collect_urls(
                    page,
                    page_url,
                    _DETAIL_URL_RE,
                    limit=max(limit * 3, 20),
                    scroll_loops=scroll_loops,
                    pause_sec=pause_sec,
                    scroll_px=scroll_px,
                    stale_rounds=stale_rounds,
                )
                content = await page.content()
                page_cards = _listing_cards(content, page_url) or page_cards
                cards.update(page_cards)
                for raw_url in urls:
                    url = _canonical_detail_url(raw_url)
                    if not _is_detail_url(url):
                        continue
                    identity = _detail_identity(url)
                    card = page_cards.get(identity)
                    if (
                        keywords
                        and card
                        and not listing_matches_keywords(
                            card.get("title", ""), card.get("text", ""), keywords
                        )
                    ):
                        continue
                    if identity in seen:
                        continue
                    seen.add(identity)
                    collected.append(url)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
                next_url = _next_listing_url(content, page_url)
                if next_url is None and urls:
                    next_url = _listing_page_url(page_url, _page_number(page_url) + 1)
                if not next_url:
                    break
                current_url = next_url
        return collected[:limit], cards

    async def _browser_detail(
        self,
        spec: CareerSiteSpec,
        url: str,
        bypass_strategy: Any,
    ) -> DiscoveredPostingPayload | None:
        config = self._browser_config(spec, bypass_strategy)
        async with open_page(
            config,
            use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
            bypass_strategy=bypass_strategy,
        ) as page:
            await navigate(page, url, config)
            final_url = _canonical_detail_url(urljoin(url, str(getattr(page, "url", url) or url)))
            content = await page.content()
            if is_challenge_response(content):
                raise _challenge_error(final_url, content)
            if not _is_detail_url(final_url) or _detail_identity(final_url) != _detail_identity(
                url
            ):
                return None
            return _detail_payload(final_url, content, spec.url)

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
            response = await safe_fetch(client, url)
        except Exception as exc:  # noqa: BLE001 - listing card covers ordinary detail loss
            if _status_from_exception(exc) in {401, 403, 429, 503}:
                raise
            if bypass_strategy is None:
                return _listing_payload(url, card, spec)

        if response is not None:
            final_url = _canonical_detail_url(str(getattr(response, "url", url) or url))
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
                payload = _detail_payload(final_url, body, spec.url)
                if payload is not None:
                    return payload

        if bypass_strategy is not None:
            payload = await self._browser_detail(spec, url, bypass_strategy)
            if payload is not None:
                return payload
        return _listing_payload(url, card, spec)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = max(1, self._limit(spec.limit))
        detail_re = self._detail_re()
        listing_url = _normalize_listing_url(spec.url)
        collected: list[str] = []
        seen: set[str] = set()
        visited: set[str] = set()
        for _ in range(self._max_listing_pages(spec, limit)):
            if listing_url in visited or len(collected) >= limit:
                break
            visited.add(listing_url)
            try:
                response = await safe_fetch(client, listing_url)
            except Exception as exc:
                if _status_from_exception(exc) == 429:
                    raise
                logger.debug("habr.listing_fetch_failed", url=listing_url, error=str(exc))
                break
            response_url = str(getattr(response, "url", listing_url) or listing_url)
            if detail_re.search(response_url):
                url = _canonical_detail_url(response_url)
                if url not in seen:
                    seen.add(url)
                    collected.append(url)
                break
            added = 0
            for identity, card in _listing_cards(str(response.text), response_url).items():
                url = _canonical_detail_url(card["url"])
                if identity in seen or not _is_detail_url(url):
                    continue
                seen.add(identity)
                collected.append(url)
                added += 1
                if len(collected) >= limit:
                    break
            if len(collected) >= limit:
                break
            if added == 0:
                break
            next_url = _next_listing_url(str(response.text), response_url)
            listing_url = next_url or _listing_page_url(
                response_url, _page_number(response_url) + 1
            )
        return collected[:limit]

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = max(1, self._limit(spec.limit))
        source_name = spec.source_name or "habr_career"
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        keywords = keywords_from_spec(spec)
        listing_url = _normalize_listing_url(spec.url)
        max_pages = self._max_listing_pages(spec, limit)
        visited: set[str] = set()
        collected: list[str] = []
        cards: dict[str, dict[str, str]] = {}
        seen: set[str] = set()
        listing_error: Exception | None = None

        for _ in range(max_pages):
            if listing_url in visited or len(collected) >= limit:
                break
            visited.add(listing_url)
            try:
                response = await safe_fetch(client, listing_url)
            except Exception as exc:  # noqa: BLE001 - browser fallback may rescue listings
                listing_error = exc
                logger.debug("habr.listing_http_failed", url=listing_url, error=str(exc))
                break
            response_url = str(getattr(response, "url", listing_url) or listing_url)
            body = str(getattr(response, "text", "") or "")
            if is_challenge_response(body):
                listing_error = _challenge_error(response_url, response)
                break
            page_cards = _listing_cards(body, response_url)
            if not page_cards:
                break
            cards.update(page_cards)
            added = 0
            for identity, card in page_cards.items():
                if keywords and not listing_matches_keywords(
                    card.get("title", ""), card.get("text", ""), keywords
                ):
                    continue
                url = _canonical_detail_url(card["url"])
                if identity in seen or not _is_detail_url(url):
                    continue
                seen.add(identity)
                collected.append(url)
                added += 1
                if len(collected) >= limit:
                    break
            stats = spec.monitor_config.get("_pipeline_stats")
            if stats is not None:
                stats.parser_urls_discovered = len(collected)
            logger.info(
                "habr.listing_page",
                page=_page_number(response_url),
                discovered=len(collected),
                added=added,
                requested=limit,
            )
            if len(collected) >= limit:
                break
            if added == 0:
                break
            next_url = _next_listing_url(body, response_url)
            listing_url = next_url or _listing_page_url(
                response_url, _page_number(response_url) + 1
            )

        if listing_error is not None:
            status = _status_from_exception(listing_error)
            if status == 429:
                raise listing_error
            if _is_challenge_error(listing_error) and bypass_strategy is None:
                raise listing_error
            if bypass_strategy is not None:
                try:
                    browser_urls, browser_cards = await self._discover_with_browser(
                        spec,
                        limit=limit,
                        bypass_strategy=bypass_strategy,
                    )
                    cards.update(browser_cards)
                    for url in browser_urls:
                        identity = _detail_identity(url)
                        if identity in seen:
                            continue
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
        detail_errors: list[Exception] = []

        async def bounded(url: str) -> DiscoveredPostingPayload | None:
            return await self._detail(
                spec,
                url,
                cards.get(_detail_identity(url), {}),
                client,
                bypass_strategy,
            )

        if detail_urls:
            semaphore = asyncio.Semaphore(self._detail_concurrency(spec))

            async def guarded(url: str) -> DiscoveredPostingPayload | None:
                async with semaphore:
                    try:
                        return await bounded(url)
                    except Exception as exc:  # noqa: BLE001 - retain partial detail output
                        detail_errors.append(exc)
                        logger.warning("habr.detail_fetch_failed", url=url, error=str(exc))
                        return None

            tasks = [asyncio.create_task(guarded(url)) for url in detail_urls]
            try:
                for task in asyncio.as_completed(tasks):
                    payload = await task
                    if payload is not None:
                        yield _payload_to_raw_item(payload, spec, source_name)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            if detail_errors:
                raise detail_errors[0]

        for url in listing_only_urls:
            payload = _listing_payload(url, cards.get(_detail_identity(url), {}), spec)
            if payload is not None:
                yield _payload_to_raw_item(payload, spec, source_name)

    @property
    def __name__(self) -> str:
        return "HabrCareerParser"


register_site_parser(
    "habr_career",
    domain_pattern=HabrCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:career.habr.com",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="Habr Career exposes SSR vacancy cards, q/type search, paginated listings, and JobPosting detail pages.",
    ),
)(HabrCareerParser)
