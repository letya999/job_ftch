"""Dedicated HTTP/browser parser for GeekJob vacancies."""

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
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.scrapers.json_ld import parse_html
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    extract_urls_with_limit,
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

_DOMAIN_PATTERN = r"^https?://(?:www\.)?geekjob\.ru(?:/|$)"
_URL_FILTER = r"geekjob\.ru/(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?$)"
_DETAIL_PATTERN = (
    r"((?:https?://(?:www\.)?geekjob\.ru)?/"
    r"(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?)(?:\?[^\"' <]*)?)"
)
_DETAIL_URL_RE = re.compile(
    r"^https?://(?:www\.)?geekjob\.ru/"
    r"(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?)(?:[?#].*)?$",
    re.IGNORECASE,
)


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _detail_identity(url: str) -> str:
    match = re.search(r"/(?:vacancy/([a-z0-9-]+)|jobs/(\d+))/?$", urlparse(url).path, re.I)
    return (match.group(1) or match.group(2)) if match else _canonical_url(url).casefold()


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
    for script in LexborHTMLParser(html.unescape(html_text)).css(
        'script[type="application/ld+json"]'
    ):
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


def _strip_html(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    tree = LexborHTMLParser(f"<div>{html.unescape(value)}</div>")
    return " ".join(tree.text(separator=" ", strip=True).split())


def _plain(value: object) -> str:
    if isinstance(value, dict):
        return _plain(value.get("name") or value.get("title") or value.get("value"))
    if isinstance(value, list):
        return ", ".join(part for item in value if (part := _plain(item)))
    if value is None:
        return ""
    return " ".join(str(value).split())


def _dom_text(tree: LexborHTMLParser, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = tree.css_first(selector)
        if node is None:
            continue
        value = " ".join(node.text(separator=" ", strip=True).split())
        if value:
            return value
    return ""


def _dom_detail(html_text: str) -> tuple[str, str, str | None, list[str] | None, str | None]:
    tree = LexborHTMLParser(html.unescape(html_text))
    title = _dom_text(tree, ("h1", ".vacancy-name"))
    company = _dom_text(tree, (".company-name", '[class*="company-name"]')) or None
    description = _dom_text(
        tree,
        (".description", ".vacancy-description", '[class*="description"]', "article"),
    )
    job_format = _dom_text(tree, (".jobformat", '[class*="jobformat"]')) or None
    location = _dom_text(tree, (".location", '[class*="location"]'))
    return title, description, company, [location] if location else None, job_format


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
    for field in ("addressLocality", "addressRegion", "addressCountry"):
        field_value = address.get(field)
        if isinstance(field_value, dict):
            field_value = field_value.get("name")
        if isinstance(field_value, str) and field_value.strip():
            parts.append(" ".join(field_value.split()))
    return ", ".join(parts) or None


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
    applicants = posting.get("applicantLocationRequirements")
    requirements = applicants if isinstance(applicants, list) else [applicants]
    for requirement in requirements:
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
    dom_title, dom_description, dom_company, dom_locations, dom_job_format = _dom_detail(
        html_text
    )
    title = str((posting or {}).get("title") or (posting or {}).get("name") or "").strip()
    description_value = (posting or {}).get("description")
    description = description_value if isinstance(description_value, str) else None
    title = title or (str(scraped.title).strip() if scraped and scraped.title else "")
    description = description or (scraped.description if scraped else None) or dom_description
    title = title or dom_title
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
        "parser": "site_geekjob",
        "board_url": board_url,
        "job_url": detail_url,
        "source_platform": "geekjob.ru",
        "detail_vacancy_confirmed": True,
        "country_authoritative": False,
    }
    if dom_job_format:
        metadata["work_mode"] = dom_job_format
    if locations:
        metadata["source_locations_schema"] = locations
    if company:
        metadata["company"] = company
        metadata["company_authoritative"] = True
    payload_data = asdict(scraped)
    payload_data["metadata"] = metadata
    return DiscoveredPostingPayload(url=detail_url, **payload_data)


def _api_detail_url(row: dict[str, Any]) -> str | None:
    raw_url = row.get("url") or row.get("link") or row.get("href")
    if isinstance(raw_url, str) and raw_url.strip():
        candidate = _canonical_url(urljoin("https://geekjob.ru/", raw_url.strip()))
        if _is_detail_url(candidate):
            return candidate
    vacancy_id = row.get("id")
    if isinstance(vacancy_id, (str, int)) and str(vacancy_id).strip():
        return urljoin("https://geekjob.ru/", f"/vacancy/{str(vacancy_id).strip()}")
    return None


def _job_format_text(value: object) -> str:
    if not isinstance(value, dict):
        return _plain(value)
    labels = {
        "remote": "remote",
        "relocate": "relocation",
        "parttime": "part-time",
        "inhouse": "office",
    }
    return ", ".join(labels[key] for key in labels if value.get(key) is True)


def _api_card(row: dict[str, Any], board_url: str) -> dict[str, str] | None:
    url = _api_detail_url(row)
    title = _plain(row.get("position") or row.get("title") or row.get("name"))
    if not url or not title:
        return None
    company = _plain(row.get("company"))
    locations = ", ".join(
        part for part in (_plain(row.get("city")), _plain(row.get("country"))) if part
    )
    format_text = _job_format_text(row.get("jobFormat"))
    card_text = "\n".join(
        part
        for part in (
            title,
            company,
            locations,
            _plain(row.get("salary")),
            format_text,
            _plain(row.get("experience")),
            _strip_html(row.get("description")),
        )
        if part
    )
    vacancy_id = str(row.get("id") or _detail_identity(url)).strip()
    return {
        "url": url,
        "title": title,
        "text": card_text,
        "id": vacancy_id,
        "board_url": board_url,
        "company": company,
        "locations": locations,
    }


def _listing_cards(html_text: str, base_url: str) -> dict[str, dict[str, str]]:
    tree = LexborHTMLParser(html.unescape(html_text))
    cards: dict[str, dict[str, str]] = {}
    for anchor in tree.css("a[href]"):
        raw_href = str(anchor.attributes.get("href", "") or "").strip()
        url = _canonical_url(urljoin(base_url, html.unescape(raw_href)))
        if not _is_detail_url(url):
            continue
        identity = _detail_identity(url)
        title = " ".join(anchor.text(separator=" ", strip=True).split())
        card: Any = anchor
        while card is not None and "vacancy" not in str(
            card.attributes.get("class") or ""
        ).casefold():
            card = card.parent
        card_text = " ".join((card or anchor).text(separator=" ", strip=True).split())
        current = cards.get(identity)
        if current is None:
            cards[identity] = {"url": url, "title": title, "text": card_text}
        else:
            current["title"] = current["title"] or title
            if len(card_text) > len(current["text"]):
                current["text"] = card_text

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
                if not _is_detail_url(url):
                    continue
                identity = _detail_identity(url)
                cards.setdefault(
                    identity,
                    {
                        "url": url,
                        "title": _plain(item.get("name") or item.get("title")),
                        "text": _plain(item.get("description")),
                    },
                )
    return cards


def _listing_payload(
    url: str,
    card: dict[str, str],
    spec: CareerSiteSpec,
) -> DiscoveredPostingPayload | None:
    title = str(card.get("title") or "").strip()
    if not title:
        return None
    card_text = str(card.get("text") or "").strip()
    description = card_text if card_text and card_text != title else None
    return DiscoveredPostingPayload(
        url=url,
        title=title,
        description=description,
        metadata={
            "parser": "site_geekjob_card",
            "board_url": spec.url,
            "job_url": url,
            "source_platform": "geekjob.ru",
            "listing_text": card_text,
            "company": card.get("company") or None,
            "locations": card.get("locations") or None,
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


def _protected_status(status: int | None) -> bool:
    return status in {401, 403, 429, 503}


def _is_challenge_error(exc: BaseException) -> bool:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    return isinstance(exc, BrowserChallengeError)


def _page_number(url: str) -> int:
    raw = dict(parse_qsl(urlparse(url).query, keep_blank_values=True)).get("page")
    return max(1, _as_int(raw, 1))


def _page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _next_listing_url(html_text: str, base_url: str) -> str | None:
    current_page = _page_number(base_url)
    tree = LexborHTMLParser(html.unescape(html_text))
    candidates: list[tuple[int, str]] = []
    for selector in ('link[rel="next"]', 'a[rel="next"]', "a[href]"):
        for node in tree.css(selector):
            href = str(node.attributes.get("href", "") or "").strip()
            if not href:
                continue
            candidate = urljoin(base_url, html.unescape(href))
            parsed = urlparse(candidate)
            if (parsed.hostname or "").casefold().removeprefix("www.") != "geekjob.ru":
                continue
            if parsed.path.rstrip("/") != urlparse(base_url).path.rstrip("/"):
                continue
            page = _as_int(dict(parse_qsl(parsed.query, keep_blank_values=True)).get("page"), 0)
            if page > current_page:
                candidates.append((page, candidate))
    return min(candidates)[1] if candidates else None


@register_site_parser(
    "geekjob",
    domain_pattern=r"(?:www\.)?geekjob\.ru(?:/|$)",
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:geekjob.ru"),
)
class GeekJobParser:
    """Parse GeekJob's JSON search cards and canonical vacancy details."""

    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "per_keyword"
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
        parsed = urlparse(base_url)
        if not parsed.path.rstrip("/").endswith("/vacancies"):
            parsed = parsed._replace(path="/vacancies")
        listing_url = urlunparse(parsed)
        return [with_query_params(listing_url, {"qs": term}) for term in terms]

    @staticmethod
    def _search_query(url: str) -> str:
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        return str(query.get("qs") or "").strip()

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=False,
            include_if_detail_page=True,
            extra={
                "listing_page_size": 20,
                "max_listing_pages": 50,
                "detail_concurrency": 4,
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 50,
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
        return value if value is not None else self._extra().get(key, default)

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return max(1, int(spec_limit))
        return max(1, _as_int(self._manifest_value("limit", 50), 50))

    def _max_listing_pages(self, spec: CareerSiteSpec, limit: int) -> int:
        configured = _as_int(self._extra_value(spec, "max_listing_pages", 50), 50)
        page_size = max(1, _as_int(self._extra_value(spec, "listing_page_size", 20), 20))
        return max(1, min(100, max(configured, (limit + page_size - 1) // page_size)))

    def _detail_concurrency(self, spec: CareerSiteSpec) -> int:
        configured = spec.monitor_config.get("detail_concurrency")
        if configured is None:
            from job_ftch.config import get_settings

            configured = get_settings().career_site_detail_concurrency
        return max(1, min(50, _as_int(configured, 4)))

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

    def _detail_re(self) -> re.Pattern[str]:
        pattern = str(self._manifest_value("detail_pattern", _DETAIL_PATTERN))
        return re.compile(pattern, re.IGNORECASE)

    async def _fetch_api_page(
        self,
        spec: CareerSiteSpec,
        client: Any,
        page: int,
    ) -> tuple[list[dict[str, Any]], Any]:
        query = self._search_query(spec.url)
        api_url = urljoin(spec.url, "/json/find/vacancy")
        params: dict[str, str] = {"page": str(page)}
        if query:
            params["qs"] = query
        response = await fetch_with_retry(
            client,
            api_url,
            params=params,
            follow_redirects=True,
        )
        response.raise_for_status()
        if is_challenge_response(str(getattr(response, "text", "") or "")):
            raise _challenge_error(api_url, response)
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return [], payload
        return [row for row in rows if isinstance(row, dict)], payload

    def _api_has_more(self, payload: Any, page: int, row_count: int, page_size: int) -> bool:
        if not isinstance(payload, dict):
            return row_count >= page_size
        page_count = _as_int(payload.get("pagecount"), 0)
        if page_count:
            return page < page_count
        next_page = payload.get("nextpage")
        if next_page is not None:
            return bool(next_page) and _as_int(next_page, page + 1) > page
        return row_count >= page_size

    async def _discover_search_api(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = self._limit(spec.limit)
        page_size = max(1, _as_int(self._extra_value(spec, "listing_page_size", 20), 20))
        urls: list[str] = []
        seen: set[str] = set()
        for page in range(1, self._max_listing_pages(spec, limit) + 1):
            rows, payload = await self._fetch_api_page(spec, client, page)
            if not rows:
                break
            added = 0
            for row in rows:
                url = _api_detail_url(row)
                if not url:
                    continue
                identity = _detail_identity(url)
                if identity in seen:
                    continue
                seen.add(identity)
                urls.append(url)
                added += 1
                if len(urls) >= limit:
                    return urls
            if not added or not self._api_has_more(payload, page, len(rows), page_size):
                break
        return urls

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
            'input[name="qs"]',
            'input[type="search"]',
            'input[placeholder*="Поиск"]',
            'input[placeholder*="поиск"]',
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
                logger.debug("geekjob.browser_search_box_failed", selector=selector, error=str(exc))
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
        scroll_loops = _as_int(getattr(browser, "scroll_loops", None), 8)
        pause_sec = _as_int(getattr(browser, "scroll_pause_ms", None), 500) / 1000
        scroll_px = _as_int(getattr(browser, "scroll_px", None), 2500)
        stale_rounds = _as_int(getattr(browser, "stale_rounds", None), 3)
        keywords = keywords_from_spec(spec)
        current_url = spec.url
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
                    page, keywords, timeout_ms=_as_int(config.get("timeout"), 30_000)
                ):
                    content = await page.content()
                    page_url = urljoin(
                        page_url, str(getattr(page, "url", page_url) or page_url)
                    )
                    if is_challenge_response(content):
                        raise _challenge_error(page_url, content)
                    page_cards = _listing_cards(content, page_url)
                urls = await browser_scroll_collect_urls(
                    page,
                    page_url,
                    _DETAIL_URL_RE,
                    limit=limit,
                    scroll_loops=scroll_loops,
                    pause_sec=pause_sec,
                    scroll_px=scroll_px,
                    stale_rounds=stale_rounds,
                )
                content = await page.content()
                cards.update(_listing_cards(content, page_url) or page_cards)
                for raw_url in urls:
                    url = _canonical_url(raw_url)
                    identity = _detail_identity(url)
                    if not _is_detail_url(url) or identity in seen:
                        continue
                    card = cards.get(identity)
                    if keywords and card and not listing_matches_keywords(
                        card.get("title", ""), card.get("text", ""), keywords
                    ):
                        continue
                    seen.add(identity)
                    collected.append(url)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
                next_url = _next_listing_url(content, page_url)
                if next_url is None and urls:
                    next_url = _page_url(page_url, _page_number(page_url) + 1)
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
            final_url = _canonical_url(urljoin(url, str(getattr(page, "url", url) or url)))
            content = await page.content()
            if is_challenge_response(content):
                raise _challenge_error(final_url, content)
            if not _is_detail_url(final_url) or _detail_identity(final_url) != _detail_identity(url):
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
        except Exception as exc:  # noqa: BLE001 - card remains a safe fallback
            if _protected_status(_status_from_exception(exc)):
                raise
            if bypass_strategy is None:
                return _listing_payload(url, card, spec)

        if response is not None:
            final_url = _canonical_url(str(getattr(response, "url", url) or url))
            if not _is_detail_url(final_url) or _detail_identity(final_url) != _detail_identity(url):
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
        """Expose phase-1 discovery for callers that invoke the parser directly."""
        limit = self._limit(spec.limit)
        if self._search_query(spec.url):
            try:
                return (await self._discover_search_api(spec, client))[:limit]
            except Exception as exc:
                if _status_from_exception(exc) == 429:
                    raise
                logger.info("geekjob.search_api_failed", url=spec.url, error=str(exc))
                return []

        detail_re = self._detail_re()
        collected: list[str] = []
        seen: set[str] = set()
        max_pages = self._max_listing_pages(spec, limit)
        for page in range(1, max_pages + 1):
            page_url = _page_url(spec.url, page)
            try:
                response = await safe_fetch(client, page_url)
            except Exception as exc:
                if _status_from_exception(exc) == 429:
                    raise
                logger.debug("geekjob.listing_fetch_failed", url=page_url, error=str(exc))
                break
            response_url = str(getattr(response, "url", page_url) or page_url)
            body = str(getattr(response, "text", "") or "")
            for url in extract_urls_with_limit(
                body, detail_re, response_url, limit, seen=seen
            ):
                collected.append(_canonical_url(url))
                if len(collected) >= limit:
                    return collected[:limit]
            next_url = _next_listing_url(body, response_url)
            if next_url:
                continue
            if page >= max_pages:
                break
        return collected[:limit]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "geekjob"
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        keywords = keywords_from_spec(spec)
        page_size = max(1, _as_int(self._extra_value(spec, "listing_page_size", 20), 20))
        cards: dict[str, dict[str, str]] = {}
        collected: list[str] = []
        seen: set[str] = set()
        listing_error: Exception | None = None
        for page in range(1, self._max_listing_pages(spec, limit) + 1):
            try:
                rows, payload = await self._fetch_api_page(spec, client, page)
            except Exception as exc:  # noqa: BLE001 - browser fallback may rescue the listing
                listing_error = exc
                logger.debug("geekjob.search_api_failed", url=spec.url, error=str(exc))
                break
            if not rows:
                break
            added = 0
            for row in rows:
                card = _api_card(row, spec.url)
                if card is None:
                    continue
                identity = card["id"] or _detail_identity(card["url"])
                if identity in seen:
                    continue
                # `qs` is fuzzy: "project manager" also returns Product/Construction
                # Manager. Keep the server query for recall, then match the title.
                if keywords and not listing_matches_keywords(
                    card.get("title", ""), card.get("text", ""), keywords
                ):
                    continue
                seen.add(identity)
                cards[_detail_identity(card["url"])] = card
                collected.append(card["url"])
                added += 1
                if len(collected) >= limit:
                    break
            stats = spec.monitor_config.get("_pipeline_stats")
            if stats is not None:
                stats.parser_urls_discovered = len(collected)
            logger.info(
                "geekjob.listing_page",
                page=page,
                discovered=len(collected),
                returned=len(rows),
                requested=limit,
            )
            if len(collected) >= limit or not added:
                break
            if not self._api_has_more(payload, page, len(rows), page_size):
                break

        if listing_error is not None:
            status = _status_from_exception(listing_error)
            if status == 429:
                raise listing_error
            if _is_challenge_error(listing_error) and bypass_strategy is None:
                raise listing_error
            if bypass_strategy is not None:
                try:
                    browser_urls, browser_cards = await self._discover_with_browser(
                        spec, limit=limit, bypass_strategy=bypass_strategy
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
        semaphore = asyncio.Semaphore(self._detail_concurrency(spec))
        detail_errors: list[Exception] = []

        async def bounded(url: str) -> DiscoveredPostingPayload | None:
            async with semaphore:
                try:
                    return await self._detail(
                        spec,
                        url,
                        cards.get(_detail_identity(url), {}),
                        client,
                        bypass_strategy,
                    )
                except Exception as exc:  # noqa: BLE001 - retain partial output, then retry source
                    detail_errors.append(exc)
                    logger.warning("geekjob.detail_fetch_failed", url=url, error=str(exc))
                    if _protected_status(_status_from_exception(exc)) or _is_challenge_error(exc):
                        raise
                    return None

        tasks = [asyncio.create_task(bounded(url)) for url in detail_urls]
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
        return "GeekJobParser"
