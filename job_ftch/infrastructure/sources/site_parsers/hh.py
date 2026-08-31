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

import html
import json
import re
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
_LISTING_TIMESTAMP_RE = re.compile(
    r'"publicationTime"\s*:\s*\{[^}]*"@timestamp"\s*:\s*(\d+)'
)
_MAX_DETAIL_REQUESTS = 10


def _is_allowed_detail_host(detail_url: str, board_url: str) -> bool:
    if is_same_site_family(detail_url, board_url=board_url):
        return True
    detail_host = (urlparse(detail_url).hostname or "").lower()
    board_host = (urlparse(board_url).hostname or "").lower()
    hh_by_hosts = {"hh.by", "rabota.by"}
    return detail_host in hh_by_hosts and board_host in hh_by_hosts


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
        normalized = urljoin(base_url, html.unescape(match)).split("&amp;")[0].split("?")[0]
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
        url = urljoin(base_url, str(anchor.attributes.get("href", "") or "")).split("?")[0]
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
    if parsed.hostname == "hh1.az" and parsed.path.rstrip("/") == "":
        return urlunparse(parsed._replace(path="/search/vacancy"))
    employer_match = re.fullmatch(r"/employer/(\d+)/?", parsed.path)
    if employer_match:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["employer_id"] = employer_match.group(1)
        return urlunparse(parsed._replace(path="/search/vacancy", query=urlencode(query)))
    return url


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
        for block in blocks:
            if not isinstance(block, dict):
                continue
            type_value = block.get("@type")
            if type_value == "JobPosting" or (
                isinstance(type_value, list) and "JobPosting" in type_value
            ):
                return block
    return None


def _item_from_detail_html(
    detail_url: str,
    html_text: str,
    source_name: str,
    board_url: str,
) -> RawItem | None:
    posting = _parse_job_posting_jsonld(html_text)
    if posting is None:
        return None

    title = str(posting.get("title") or posting.get("name") or "").strip()
    description_html = posting.get("description")
    description = _strip_html(description_html if isinstance(description_html, str) else None)
    if not title and not description:
        return None

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

    def runtime_defaults(self, url: str) -> dict[str, str]:
        del url
        return {"url_filter": _URL_FILTER}

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

    def _page_count(self, limit: int) -> int:
        manifest_entry = getattr(self, "_manifest_entry", None)
        extra = getattr(manifest_entry, "extra", {}) if manifest_entry is not None else {}
        page_size = int(extra.get("listing_page_size", 20))
        max_pages = int(extra.get("max_listing_pages", 5))
        return max(1, min(max_pages, (limit // page_size) + 1))

    async def _discover_with_browser(
        self,
        spec: CareerSiteSpec,
        *,
        limit: int,
        bypass_strategy: Any,
    ) -> list[str]:
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        browser_config = {
            "headless": True,
            "stealth": True,
            "wait": getattr(browser, "wait", None) or "domcontentloaded",
        }
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            urls = await browser_scroll_collect_urls(
                page,
                getattr(page, "url", spec.url) or spec.url,
                _DETAIL_URL_RE,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 6,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
                scroll_px=getattr(browser, "scroll_px", None) or 2500,
                stale_rounds=getattr(browser, "stale_rounds", None) or 2,
            )
        return [url for url in urls if _is_allowed_detail_host(url, spec.url)]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "hh_jobs"
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        effective_client = client
        if bypass_strategy is not None and hasattr(bypass_strategy, "apply_http"):
            try:
                effective_client = await bypass_strategy.apply_http(client)
            except Exception as exc:
                logger.debug("hh.bypass_apply_http_fallback", error=str(exc))
                effective_client = client

        collected_urls: list[str] = []
        listing_snapshots: dict[str, tuple[str, datetime | None]] = {}
        seen_detail_ids: set[str] = set()
        page_count = self._page_count(limit)
        captcha_detected = False
        for page in range(page_count):
            listing_url = _listing_page_url(_normalize_listing_url(spec.url), page)
            response = await effective_client.get(listing_url, follow_redirects=True)
            response.raise_for_status()
            if is_challenge_response(response.text):
                logger.warning(
                    "hh.captcha_detected_listing",
                    url=listing_url,
                    page=page,
                )
                captcha_detected = True
                break
            page_urls = _extract_vacancy_urls(
                response.text,
                str(response.url),
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
            listing_snapshots.update(
                _extract_listing_snapshots(response.text, str(response.url))
            )
            new_urls = [
                url
                for url in page_urls
                if _detail_identity(url) not in seen_detail_ids
                and _is_allowed_detail_host(url, spec.url)
            ]
            if not new_urls:
                break
            for url in new_urls:
                seen_detail_ids.add(_detail_identity(url))
                collected_urls.append(url)
                if len(collected_urls) >= limit:
                    break
            if len(collected_urls) >= limit:
                break

        if not collected_urls and bypass_strategy is not None and not captcha_detected:
            try:
                collected_urls = await self._discover_with_browser(
                    spec,
                    limit=limit,
                    bypass_strategy=bypass_strategy,
                )
            except Exception as exc:
                logger.debug("hh.browser_discover_failed", url=spec.url, error=str(exc))

        seen_final_ids: set[str] = set()
        details_blocked = False
        detail_requests = 0
        for detail_url in collected_urls[:limit]:
            if details_blocked or detail_requests >= _MAX_DETAIL_REQUESTS:
                snapshot = listing_snapshots.get(_detail_identity(detail_url))
                if snapshot:
                    yield _item_from_listing(detail_url, *snapshot, source_name, spec.url)
                continue
            detail_requests += 1
            response = await effective_client.get(detail_url, follow_redirects=True)
            response.raise_for_status()
            if is_challenge_response(response.text):
                logger.warning("hh.captcha_detected_detail", url=detail_url)
                details_blocked = True
                snapshot = listing_snapshots.get(_detail_identity(detail_url))
                if snapshot:
                    yield _item_from_listing(detail_url, *snapshot, source_name, spec.url)
                continue
            final_url = str(response.url)
            final_id = _detail_identity(final_url)
            if final_id in seen_final_ids:
                continue
            seen_final_ids.add(final_id)
            item = _item_from_detail_html(
                final_url,
                response.text,
                source_name,
                spec.url,
            )
            if item is not None:
                yield item
            else:
                snapshot = listing_snapshots.get(final_id)
                if snapshot:
                    yield _item_from_listing(final_url, *snapshot, source_name, spec.url)

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
