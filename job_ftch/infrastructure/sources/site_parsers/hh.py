"""Site-specific parser for hh.ru / hh.kz / hh.by / hh.uz / headhunter.kg search listings.

These pages are server-rendered enough to expose stable vacancy links over
plain HTTP, while the generic DOM/browser path is expensive and unreliable on
live anti-bot flows. This parser takes the cheap path:

1. fetch the search/listing HTML over HTTP;
2. extract stable ``/vacancy/<id>`` links;
3. fetch each detail page over HTTP;
4. parse ``JobPosting`` JSON-LD from the detail page and emit ``RawItem``.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    is_challenge_response,
    normalize_search_keywords,
    resolve_browser_config,
    with_query_params,
)
from job_ftch.infrastructure.sources.url_scoring import is_same_site_family

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DOMAIN_PATTERN = r"^https?://(?:(?:[a-z0-9-]+\.)?hh\.(?:ru|kz|uz|by)|hh1\.az|(?:[a-z0-9-]+\.)?headhunter\.kg)(?:/|$)"
_DETAIL_URL_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?(?:hh\.(?:ru|kz|uz|by)|hh1\.az|headhunter\.kg|rabota\.by)/vacancy/(\d+)",
    re.IGNORECASE,
)
_JSONLD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_VACANCY_LINK_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?(?:hh\.(?:ru|kz|uz|by)|hh1\.az|headhunter\.kg|rabota\.by)/vacancy/\d+[^\"' <]*",
    re.IGNORECASE,
)
_URL_FILTER = (
    r"(?:[a-z0-9-]+\.)?(?:hh\.(?:ru|kz|uz|by)|hh1\.az|headhunter\.kg|rabota\.by)/vacancy/\d+"
)
_LISTING_TIMESTAMP_RE = re.compile(r'"publicationTime"\s*:\s*\{[^}]*"@timestamp"\s*:\s*(\d+)')
_PROXY_DOMAINS = [
    "hh.ru",
    "hh.kz",
    "hh.uz",
    "hh.by",
    "hh1.az",
    "headhunter.kg",
    "rabota.by",
]


def _is_allowed_detail_host(detail_url: str, board_url: str) -> bool:
    if is_same_site_family(detail_url, board_url=board_url):
        return True
    detail_host = (urlparse(detail_url).hostname or "").lower()
    board_host = (urlparse(board_url).hostname or "").lower()
    hh_by_hosts = {"hh.by", "rabota.by"}
    return detail_host in hh_by_hosts and board_host in hh_by_hosts


def _canonical_detail_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _browser_challenge_error(url: str, body: str, challenge_type: str) -> Exception:
    from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

    return BrowserChallengeError(
        url=url,
        status_code=None,
        headers={},
        body=body.encode(),
        challenge_type=challenge_type,
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    fragment = HTMLParser(f"<div>{html.unescape(value)}</div>")
    return " ".join(fragment.body.text(separator=" ", strip=True).split()) if fragment.body else ""


def _extract_vacancy_urls(html_text: str, base_url: str, *, limit: int) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    raw_html = html.unescape(html_text)
    tree = HTMLParser(raw_html)
    candidates = list(_VACANCY_LINK_RE.findall(raw_html))
    candidates.extend(
        str(anchor.attributes.get("href", "") or "") for anchor in tree.css("a[href]")
    )
    for match in candidates:
        normalized = _canonical_detail_url(urljoin(base_url, html.unescape(match)))
        if not _DETAIL_URL_RE.search(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


def _extract_listing_snapshots(
    html_text: str, base_url: str
) -> dict[str, tuple[str, datetime | None]]:
    raw_html = html.unescape(html_text)
    tree = HTMLParser(raw_html)
    snapshots: dict[str, tuple[str, datetime | None]] = {}
    for anchor in tree.css("a[href]"):
        url = _canonical_detail_url(urljoin(base_url, str(anchor.attributes.get("href", "") or "")))
        identity = _detail_identity(url)
        if not _DETAIL_URL_RE.search(url):
            continue
        title = " ".join(anchor.text(separator=" ", strip=True).split())
        if title:
            card_start = raw_html.find(f'id="{identity}"')
            timestamp_match = (
                _LISTING_TIMESTAMP_RE.search(raw_html, card_start, card_start + 20_000)
                if card_start >= 0
                else None
            )
            published_at = (
                datetime.fromtimestamp(int(timestamp_match.group(1)), tz=UTC)
                if timestamp_match
                else None
            )
            snapshots.setdefault(identity, (title, published_at))
    return snapshots


def _listing_page_url(url: str, page: int) -> str:
    if page <= 0:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _normalize_listing_url(url: str) -> str:
    """Use a vacancy listing when a supported HH URL is not itself one."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/")
    if not path or path == "/search":
        return urlunparse(parsed._replace(path="/search/vacancy"))
    employer_match = re.fullmatch(r"/employer/(\d+)/?", parsed.path)
    if employer_match:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["employer_id"] = employer_match.group(1)
        return urlunparse(parsed._replace(path="/search/vacancy", query=urlencode(query)))
    if host and (path != "/search/vacancy" and not path.startswith("/search/vacancy/")):
        return urlunparse(parsed._replace(path="/search/vacancy"))
    return url


def _current_page_number(url: str) -> int:
    raw_page = dict(parse_qsl(urlparse(url).query, keep_blank_values=True)).get("page")
    try:
        return max(0, int(raw_page or 0))
    except (TypeError, ValueError):
        return 1


def _next_sequential_listing_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(_current_page_number(url) + 1)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _extract_next_listing_url(html_text: str, base_url: str) -> str | None:
    """Find HH's explicit next page before falling back to a page counter."""
    tree = HTMLParser(html.unescape(html_text))
    for selector in (
        'link[rel="next"]',
        'a[rel="next"]',
        'a[data-qa*="pager-next"]',
        'a[data-qa*="pagination-next"]',
    ):
        for node in tree.css(selector):
            href = str(node.attributes.get("href", "") or "").strip()
            if href:
                return urljoin(base_url, href).split("&amp;")[0]

    current_page = _current_page_number(base_url)
    candidates: list[tuple[int, str]] = []
    for node in tree.css("a[href]"):
        href = str(node.attributes.get("href", "") or "")
        candidate = urljoin(base_url, html.unescape(href))
        parsed = urlparse(candidate)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        try:
            page = int(query.get("page", ""))
        except ValueError:
            continue
        if page > current_page and parsed.path.rstrip("/") == "/search/vacancy":
            candidates.append((page, candidate))
    if candidates:
        return min(candidates)[1]
    return None


def _detail_identity(url: str) -> str:
    """Collapse regional HH host aliases for the same vacancy before fetching."""
    match = _DETAIL_URL_RE.search(url)
    return match.group(1) if match else url.casefold()


def _parse_job_posting_jsonld(html_text: str) -> dict[str, Any] | None:
    for raw_block in _JSONLD_RE.findall(html_text):
        raw = raw_block.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks = parsed if isinstance(parsed, list) else [parsed]
        pending = list(blocks)
        while pending:
            block = pending.pop(0)
            if isinstance(block, dict) and isinstance(block.get("@graph"), list):
                pending.extend(block["@graph"])
            if not isinstance(block, dict):
                continue
            type_value = block.get("@type")
            if type_value == "JobPosting" or (
                isinstance(type_value, list) and "JobPosting" in type_value
            ):
                return block
    return None


def _item_from_detail_dom(
    detail_url: str,
    html_text: str,
    source_name: str,
    board_url: str,
) -> RawItem | None:
    if is_challenge_response(html_text):
        return None
    tree = HTMLParser(html.unescape(html_text))

    def _text(selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            node = tree.css_first(selector)
            if node is not None:
                value = " ".join(node.text(separator=" ", strip=True).split())
                if value:
                    return value
        return ""

    title = _text((
        '[data-qa="vacancy-title"]',
        'h1[data-qa*="vacancy"]',
        "h1",
    ))
    description = _text((
        '[data-qa="vacancy-description"]',
        '[data-qa*="vacancy-description"]',
    ))
    if not title or not description:
        return None

    company = _text((
        '[data-qa="vacancy-company-name"]',
        '[data-qa*="vacancy-company"]',
    )) or None
    location = _text((
        '[data-qa="vacancy-serp__vacancy-address"]',
        '[data-qa*="vacancy-address"]',
    ))
    external_id_match = _DETAIL_URL_RE.search(detail_url)
    external_id = external_id_match.group(1) if external_id_match else detail_url
    text_parts = [title]
    if company:
        text_parts.append(company)
    if location:
        text_parts.append(location)
    text_parts.append(description)
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=detail_url,
        text="\n".join(text_parts),
        metadata={
            "board_url": board_url,
            "job_url": detail_url,
            "company": company,
            "locations": [location] if location else None,
            "parser": "site_hh_dom",
            "detail_vacancy_confirmed": True,
        },
    )


def _item_from_detail_html(
    detail_url: str,
    html_text: str,
    source_name: str,
    board_url: str,
) -> RawItem | None:
    if is_challenge_response(html_text):
        return None
    posting = _parse_job_posting_jsonld(html_text)
    if posting is None:
        return _item_from_detail_dom(detail_url, html_text, source_name, board_url)

    title = str(posting.get("title") or posting.get("name") or "").strip()
    description_html = posting.get("description")
    description = _strip_html(description_html if isinstance(description_html, str) else None)
    if not title or not description:
        return _item_from_detail_dom(detail_url, html_text, source_name, board_url)

    organization = posting.get("hiringOrganization")
    company_name = None
    if isinstance(organization, dict):
        name = organization.get("name")
        if isinstance(name, str) and name.strip():
            company_name = name.strip()

    location_values: list[str] = []
    raw_locations = posting.get("jobLocation")
    if isinstance(raw_locations, list):
        location_items = raw_locations
    elif raw_locations is None:
        location_items = []
    else:
        location_items = [raw_locations]
    for item in location_items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if isinstance(address, dict):
            parts = []
            for field in ("addressLocality", "addressRegion", "addressCountry"):
                value = address.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            if parts:
                location_values.append(", ".join(parts))

    external_id_match = _DETAIL_URL_RE.search(detail_url)
    external_id = external_id_match.group(1) if external_id_match else detail_url

    text_parts = [title]
    if company_name:
        text_parts.append(company_name)
    text_parts.extend(location_values)
    if description:
        text_parts.append(description)

    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=detail_url,
        text="\n".join(part for part in text_parts if part),
        created_at=_parse_iso_datetime(
            posting.get("datePosted") if isinstance(posting.get("datePosted"), str) else None
        ),
        metadata={
            "board_url": board_url,
            "job_url": detail_url,
            "company": company_name,
            "locations": location_values or None,
            "employment_type": posting.get("employmentType"),
            "parser": "site_hh_jobs",
            "detail_vacancy_confirmed": True,
        },
    )


def _item_from_listing(
    detail_url: str,
    title: str,
    published_at: datetime | None,
    source_name: str,
    board_url: str,
) -> RawItem:
    external_id = _detail_identity(detail_url)
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=detail_url,
        text=title,
        created_at=published_at,
        metadata={
            "board_url": board_url,
            "job_url": detail_url,
            "parser": "site_hh_listing",
            "detail_vacancy_confirmed": False,
        },
    )


class HhParser:
    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    supports_search = True
    search_mode = "combined"

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
        # HH query language uses uppercase boolean operators; `ored_clusters`
        # keeps facet clusters OR-combined. `search_field=name` restricts the
        # combined OR-query to the vacancy TITLE — without it, generic terms like
        # "manager"/"specialist" match job descriptions and flood the first page
        # with sales/CRM noise (5288 vs 436 title-matched results, verified live).
        # Existing params (area, salary) are preserved; a non-search path is
        # normalised to the vacancy search.
        parsed = urlparse(_normalize_listing_url(base_url))
        if not parsed.path.rstrip("/").endswith("/search/vacancy"):
            parsed = parsed._replace(path="/search/vacancy")
        return [
            with_query_params(
                urlunparse(parsed),
                {
                    "text": " OR ".join(terms),
                    "search_field": "name",
                    "ored_clusters": "true",
                },
            )
        ]

    def runtime_defaults(self, url: str) -> dict[str, Any]:
        del url
        return {
            "url_filter": _URL_FILTER,
            "captcha_authorized_domains": list(_PROXY_DOMAINS),
            "proxy_rescue_allow_domains": list(_PROXY_DOMAINS),
        }

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _manifest_value(self, key: str, default: object) -> object:
        manifest_entry = getattr(self, "_manifest_entry", None)
        value = getattr(manifest_entry, key, None) if manifest_entry is not None else None
        return default if value is None else value

    def _limit(self, spec_limit: int | None) -> int:
        if spec_limit is not None:
            return spec_limit
        raw_limit = self._manifest_value("limit", 50)
        if isinstance(raw_limit, (int, str, float)):
            return int(raw_limit)
        return 50

    def _page_count(self, limit: int, page_size: int | None = None) -> int:
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        page_size = max(1, int(page_size if page_size is not None else extra.get("listing_page_size", 20)))
        return max(1, min(50, (limit + page_size - 1) // page_size))

    def _extra(self) -> dict[str, Any]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        return extra if isinstance(extra, dict) else {}

    def _max_listing_pages(self, limit: int | None = None) -> int:
        configured = max(1, int(self._extra().get("max_listing_pages", 50)))
        requested = self._page_count(limit) if limit is not None else 1
        return min(50, max(configured, requested))

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

    def _detail_concurrency(self) -> int:
        configured = self._extra().get("detail_concurrency")
        if configured is None:
            from job_ftch.config import get_settings

            configured = get_settings().career_site_detail_concurrency
        try:
            return max(1, int(configured))
        except (TypeError, ValueError):
            return 4

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
            'input[data-qa="search-input"]',
            'input[name="text"]',
            'input[type="search"]',
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
            except Exception as exc:
                logger.debug("hh.browser_search_box_failed", selector=selector, error=str(exc))
        return False

    async def _discover_with_browser(
        self,
        spec: CareerSiteSpec,
        *,
        limit: int,
        bypass_strategy: Any,
    ) -> tuple[list[str], dict[str, tuple[str, datetime | None]]]:
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        browser_config = self._browser_config(spec, bypass_strategy)
        listing_snapshots: dict[str, tuple[str, datetime | None]] = {}
        collected: list[str] = []
        seen: set[str] = set()
        visited_pages: set[str] = set()
        current_url = _normalize_listing_url(spec.url)
        keywords = normalize_search_keywords(spec.monitor_config.get("_search_keywords"))
        scroll_loops = int(getattr(browser, "scroll_loops", None) or 12)
        stale_rounds = int(getattr(browser, "stale_rounds", None) or 3)
        pause_sec = (getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0
        scroll_px = getattr(browser, "scroll_px", None) or 2500
        max_pages = self._max_listing_pages(limit)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            page._captcha_image_module = "yandexwavelatin"
            for page_index in range(50):
                if page_index >= max_pages:
                    break
                if current_url in visited_pages:
                    break
                visited_pages.add(current_url)
                await navigate(page, current_url, browser_config)
                page_url = urljoin(current_url, str(getattr(page, "url", current_url) or current_url))
                content = await page.content()
                observed_challenge = getattr(bypass_strategy, "observed_challenge_type", None)
                if isinstance(observed_challenge, str) and observed_challenge.strip():
                    raise _browser_challenge_error(
                        page_url,
                        content,
                        observed_challenge.strip(),
                    )
                if is_challenge_response(content):
                    from job_ftch.infrastructure.sources.monitors.shared import (
                        BrowserChallengeError,
                    )

                    raise BrowserChallengeError(
                        url=page_url,
                        status_code=None,
                        headers={},
                        body=content.encode(),
                        challenge_type="captcha",
                    )
                if not _extract_vacancy_urls(content, page_url, limit=1) and keywords:
                    await self._browser_search_box(
                        page,
                        keywords,
                        timeout_ms=int(browser_config.get("timeout", 30_000)),
                    )
                    content = await page.content()
                    page_url = urljoin(page_url, str(getattr(page, "url", page_url) or page_url))
                    observed_challenge = getattr(bypass_strategy, "observed_challenge_type", None)
                    if isinstance(observed_challenge, str) and observed_challenge.strip():
                        raise _browser_challenge_error(
                            page_url,
                            content,
                            observed_challenge.strip(),
                        )
                    if is_challenge_response(content):
                        from job_ftch.infrastructure.sources.monitors.shared import (
                            BrowserChallengeError,
                        )

                        raise BrowserChallengeError(
                            url=page_url,
                            status_code=None,
                            headers={},
                            body=content.encode(),
                            challenge_type="captcha",
                        )
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
                listing_snapshots.update(_extract_listing_snapshots(content, page_url))
                before = len(collected)
                for url in urls:
                    if not _is_allowed_detail_host(url, spec.url):
                        continue
                    url = _canonical_detail_url(url)
                    identity = _detail_identity(url)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    collected.append(url)
                    if len(collected) >= limit:
                        return collected, listing_snapshots
                added = len(collected) - before
                if added:
                    max_pages = max(
                        max_pages, min(50, page_index + 1 + self._page_count(limit - len(collected), added))
                    )
                next_url = _extract_next_listing_url(content, page_url)
                if next_url is None and urls:
                    next_url = _next_sequential_listing_url(page_url)
                if not next_url or next_url in visited_pages:
                    break
                current_url = next_url
        return collected[:limit], listing_snapshots

    async def _detail_with_browser(
        self,
        spec: CareerSiteSpec,
        detail_url: str,
        source_name: str,
        bypass_strategy: Any,
    ) -> RawItem | None:
        browser_config = self._browser_config(spec, bypass_strategy)
        async with open_page(
            browser_config,
            use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
            bypass_strategy=bypass_strategy,
        ) as page:
            page._captcha_image_module = "yandexwavelatin"
            await navigate(page, detail_url, browser_config)
            final_url = _canonical_detail_url(
                urljoin(detail_url, str(getattr(page, "url", detail_url) or detail_url))
            )
            content = await page.content()
            observed_challenge = getattr(bypass_strategy, "observed_challenge_type", None)
            if isinstance(observed_challenge, str) and observed_challenge.strip():
                raise _browser_challenge_error(
                    final_url,
                    content,
                    observed_challenge.strip(),
                )
            if is_challenge_response(content):
                from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

                raise BrowserChallengeError(
                    url=final_url,
                    status_code=None,
                    headers={},
                    body=content.encode(),
                    challenge_type="captcha",
                )
            return _item_from_detail_html(final_url, content, source_name, spec.url)

    async def _extract_detail_item(
        self,
        spec: CareerSiteSpec,
        detail_url: str,
        source_name: str,
        effective_client: Any,
        listing_snapshots: dict[str, tuple[str, datetime | None]],
        bypass_strategy: Any,
        browser_detail: Any = None,
    ) -> RawItem | None:
        response: Any | None = None
        try:
            response = await effective_client.get(detail_url, follow_redirects=True)
            response.raise_for_status()
        except Exception as exc:
            logger.debug("hh.detail_http_failed", url=detail_url, error=str(exc))
            response = None
            if bypass_strategy is None:
                snapshot = listing_snapshots.get(_detail_identity(detail_url))
                if snapshot:
                    return _item_from_listing(detail_url, *snapshot, source_name, spec.url)
                raise

        if response is not None and is_challenge_response(response.text):
            logger.warning("hh.captcha_detected_detail", url=detail_url)
            if bypass_strategy is None:
                from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

                raise BrowserChallengeError(
                    url=detail_url,
                    status_code=getattr(response, "status_code", None),
                    headers=dict(getattr(response, "headers", {}) or {}),
                    body=(getattr(response, "content", b"") or response.text.encode()),
                    challenge_type="captcha",
                )
            snapshot = listing_snapshots.get(_detail_identity(detail_url))
            if snapshot is not None:
                logger.info("hh.detail_captcha_uses_listing", url=detail_url)
                return _item_from_listing(detail_url, *snapshot, source_name, spec.url)
            response = None

        item = None
        final_url = detail_url
        if response is not None:
            final_url = _canonical_detail_url(str(response.url or detail_url))
            item = _item_from_detail_html(
                final_url,
                response.text,
                source_name,
                spec.url,
            )
        if item is None and bypass_strategy is not None:
            if browser_detail is not None:
                item = await browser_detail(detail_url)
            else:
                item = await self._detail_with_browser(
                    spec,
                    detail_url,
                    source_name,
                    bypass_strategy,
                )
            final_url = str(getattr(item, "url", None) or final_url) if item else final_url
        if item is not None:
            return item
        snapshot = listing_snapshots.get(_detail_identity(final_url))
        if snapshot:
            return _item_from_listing(final_url, *snapshot, source_name, spec.url)
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "hh_jobs"
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        effective_client = client

        collected_urls: list[str] = []
        listing_snapshots: dict[str, tuple[str, datetime | None]] = {}
        seen_detail_ids: set[str] = set()
        listing_url = _normalize_listing_url(spec.url)
        max_pages = self._max_listing_pages(limit)
        captcha_detected = False
        listing_error: Exception | None = None
        visited_listing_urls: set[str] = set()
        for page_index in range(50):
            if page_index >= max_pages:
                break
            if listing_url in visited_listing_urls:
                break
            visited_listing_urls.add(listing_url)
            try:
                response = await effective_client.get(listing_url, follow_redirects=True)
                response.raise_for_status()
            except Exception as exc:
                listing_error = exc
                logger.debug("hh.listing_http_failed", url=listing_url, error=str(exc))
                break
            response_url = str(response.url or listing_url)
            if is_challenge_response(response.text):
                logger.warning(
                    "hh.captcha_detected_listing",
                    url=listing_url,
                    page=page_index,
                )
                captcha_detected = True
                break
            page_urls = _extract_vacancy_urls(
                response.text,
                response_url,
                limit=max(
                    limit
                    * int(
                        getattr(getattr(self, "_manifest_entry", None), "extra", {}).get(
                            "listing_page_multiplier", 3
                        )
                    ),
                    20,
                ),
            )
            listing_snapshots.update(_extract_listing_snapshots(response.text, response_url))
            page_candidates: dict[str, str] = {}
            for url in page_urls:
                identity = _detail_identity(url)
                if identity not in seen_detail_ids and _is_allowed_detail_host(url, spec.url):
                    page_candidates.setdefault(identity, url)
            new_urls = list(page_candidates.values())
            if not new_urls:
                break
            for url in new_urls:
                seen_detail_ids.add(_detail_identity(url))
                collected_urls.append(url)
                if len(collected_urls) >= limit:
                    break
            remaining = limit - len(collected_urls)
            if remaining > 0:
                max_pages = max(
                    max_pages,
                    min(50, page_index + 1 + self._page_count(remaining, len(new_urls))),
                )
            logger.info(
                "hh.listing_page", page=page_index, added=len(new_urls),
                discovered=len(collected_urls), max_pages=max_pages, requested=limit,
            )
            stats = spec.monitor_config.get("_pipeline_stats")
            if stats is not None:
                stats.parser_urls_discovered = len(collected_urls)
            if len(collected_urls) >= limit:
                break
            next_url = _extract_next_listing_url(response.text, response_url)
            if next_url is None and new_urls:
                next_url = _next_sequential_listing_url(response_url)
            if not next_url:
                break
            listing_url = next_url

        if bypass_strategy is not None and (
            not collected_urls or listing_error is not None or captcha_detected
        ):
            try:
                browser_urls, browser_snapshots = await self._discover_with_browser(
                    spec,
                    limit=limit,
                    bypass_strategy=bypass_strategy,
                )
                listing_snapshots.update(browser_snapshots)
                for url in browser_urls:
                    identity = _detail_identity(url)
                    if identity not in seen_detail_ids:
                        seen_detail_ids.add(identity)
                        collected_urls.append(url)
                        if len(collected_urls) >= limit:
                            break
            except Exception as exc:
                logger.debug("hh.browser_discover_failed", url=spec.url, error=str(exc))
                from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError

                if isinstance(exc, BrowserChallengeError) or captcha_detected:
                    raise

        if not collected_urls and listing_error is not None and bypass_strategy is None:
            raise listing_error

        detail_limit = spec.detail_limit
        if detail_limit is None:
            from job_ftch.config import get_settings

            detail_limit = get_settings().career_site_default_detail_limit
        unique_urls: list[str] = []
        seen_final_ids: set[str] = set()
        for detail_url in collected_urls[:limit]:
            detail_id = _detail_identity(detail_url)
            if detail_id in seen_final_ids:
                continue
            seen_final_ids.add(detail_id)
            unique_urls.append(detail_url)

        detail_urls = (
            unique_urls
            if detail_limit is None
            else unique_urls[: max(0, int(detail_limit))]
        )
        listing_only_urls = unique_urls[len(detail_urls) :]
        known_detail_ids = {str(value) for value in spec.monitor_config.get("_skip_detail_ids", ())}
        if detail_urls:
            semaphore = asyncio.Semaphore(self._detail_concurrency())
            browser_lock = asyncio.Lock()
            browser_stack = AsyncExitStack()
            shared_page: Any = None
            detail_errors: list[Exception] = []
            browser_config = self._browser_config(spec, bypass_strategy)

            async def _browser_detail(url: str) -> RawItem | None:
                nonlocal shared_page
                async with browser_lock:
                    if shared_page is None:
                        native_first = (
                            getattr(bypass_strategy, "current_name", None) == "noop"
                            and not getattr(bypass_strategy, "uses_proxy", False)
                        )
                        shared_page = await browser_stack.enter_async_context(
                            open_page(
                                browser_config,
                                use_proxy=bool(getattr(bypass_strategy, "uses_proxy", False)),
                                bypass_strategy=None if native_first else bypass_strategy,
                            )
                        )
                        shared_page._captcha_image_module = "yandexwavelatin"
                    try:
                        await navigate(shared_page, url, browser_config)
                        final_url = urljoin(url, str(getattr(shared_page, "url", url) or url))
                        content = await shared_page.content()
                        if is_challenge_response(content):
                            raise _browser_challenge_error(final_url, content, "image")
                        return _item_from_detail_html(
                            _canonical_detail_url(final_url), content, source_name, spec.url
                        )
                    except BaseException:
                        await browser_stack.aclose()
                        shared_page = None
                        raise

            async def _bounded_detail(url: str) -> RawItem | None:
                if _detail_identity(url) in known_detail_ids:
                    return None
                async with semaphore:
                    try:
                        return await self._extract_detail_item(
                            spec,
                            url,
                            source_name,
                            effective_client,
                            listing_snapshots,
                            bypass_strategy,
                            browser_detail=_browser_detail,
                        )
                    except Exception as exc:
                        detail_errors.append(exc)
                        logger.warning("hh.detail_card_failed", url=url, error=str(exc))
                        return None

            tasks = [asyncio.create_task(_bounded_detail(url)) for url in detail_urls]
            try:
                for completed in asyncio.as_completed(tasks):
                    item = await completed
                    if item is not None:
                        yield item
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                await browser_stack.aclose()
            if detail_errors:
                raise detail_errors[0]

        for detail_url in listing_only_urls:
            snapshot = listing_snapshots.get(_detail_identity(detail_url))
            if snapshot:
                yield _item_from_listing(detail_url, *snapshot, source_name, spec.url)

    @property
    def __name__(self) -> str:
        return "HhParser"


register_site_parser(
    "hh",
    domain_pattern=HhParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hh",
        has_stable_url=True,
        has_publication_time=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=True,
        ordered_by_newest=True,
        item_level_dates=True,
        requires_full_snapshot=False,
        rationale="HeadHunter SSR listings expose stable vacancy URLs and detail pages embed JobPosting JSON-LD, so a dedicated HTTP parser is more reliable than the generic browser path.",
    ),
)(HhParser)
