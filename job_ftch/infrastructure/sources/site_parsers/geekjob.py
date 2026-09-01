"""Discover-and-parse parser for GeekJob listings."""

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
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    extract_urls_with_limit,
    normalize_search_keywords,
    resolve_browser_config,
    safe_fetch,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

logger = structlog.get_logger(__name__)

_DETAIL_PATTERN = (
    r"((?:https?://(?:www\.)?geekjob\.ru)?/"
    r"(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?)(?:\?[^\"' <]*)?)"
)
_JSONLD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    fragment = HTMLParser(f"<div>{html.unescape(value)}</div>")
    return " ".join(fragment.body.text(separator=" ", strip=True).split()) if fragment.body else ""


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

    external_id_match = re.search(r"/(?:vacancy/([a-z0-9-]+)|jobs/(\d+))", detail_url)
    if external_id_match:
        external_id = external_id_match.group(1) or external_id_match.group(2)
    else:
        external_id = detail_url

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
            "parser": "site_geekjob",
            "detail_vacancy_confirmed": True,
        },
    )


@register_site_parser(
    "geekjob",
    domain_pattern=r"(?:www\.)?geekjob\.ru(?:/|$)",
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:geekjob.ru"),
)
class GeekJobParser:
    """Discover and parse detail pages from static and lazy-loaded GeekJob listings."""

    domain_pattern = r"(?:www\.)?geekjob\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = True
    supports_search = True
    search_mode = "per_keyword"

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
        # The HTML page exposes the box as `qs`, while the real result surface
        # is the JSON endpoint below. One request per role avoids relying on an
        # undocumented boolean operator in GeekJob's search syntax.
        listing_url = urlunparse(parsed)
        return [with_query_params(listing_url, {"qs": term}) for term in terms]

    @staticmethod
    def _search_query(url: str) -> str:
        query = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
        return str(query.get("qs") or "").strip()

    async def _discover_search_api(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        query = self._search_query(spec.url)
        if not query:
            return []
        api_url = urljoin(spec.url, "/json/find/vacancy")
        response = await fetch_with_retry(
            client,
            api_url,
            params={"page": "1", "qs": query},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        urls: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            vacancy_id = row.get("id")
            if isinstance(vacancy_id, (str, int)) and str(vacancy_id).strip():
                urls.append(urljoin(spec.url, f"/vacancy/{str(vacancy_id).strip()}"))
            if len(urls) >= self._limit(spec.limit):
                break
        return urls

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"geekjob\.ru/(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?$)",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> None:
        del url
        return None

    def _manifest_value(self, key: str, default: object) -> object:
        manifest_entry = getattr(self, "_manifest_entry", None)
        value = getattr(manifest_entry, key, None) if manifest_entry is not None else None
        return default if value is None else value

    def _detail_re(self) -> re.Pattern[str]:
        pattern = str(self._manifest_value("detail_pattern", _DETAIL_PATTERN))
        return re.compile(pattern, re.IGNORECASE)

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

    def _listing_page_url(self, url: str, page: int) -> str:
        if page <= 1:
            return url
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = self._limit(spec.limit)
        detail_re = self._detail_re()

        if self._search_query(spec.url):
            try:
                urls = await self._discover_search_api(spec, client)
            except Exception as exc:  # noqa: BLE001 - HTML remains a safe fallback
                logger.info("geekjob.search_api_failed", url=spec.url, error=str(exc))
                urls = []
            if urls:
                return urls[:limit]

        page_count = self._page_count(limit)
        collected: list[str] = []
        listing_url = spec.url
        if self._search_query(spec.url):
            parsed = urlparse(spec.url)
            listing_url = urlunparse(parsed._replace(query=""))

        for page in range(1, page_count + 1):
            page_url = self._listing_page_url(listing_url, page)
            try:
                response = await safe_fetch(client, page_url)
            except Exception as exc:
                logger.debug("geekjob.listing_fetch_failed", url=page_url, error=str(exc))
                break
            for url in extract_urls_with_limit(
                str(response.text),
                detail_re,
                str(getattr(response, "url", listing_url) or listing_url),
                limit,
            ):
                collected.append(url)
                if len(collected) >= limit:
                    return collected[:limit]
            if not collected:
                continue

        if collected:
            return collected[:limit]

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, listing_url, browser_config)
            urls = await browser_scroll_collect_urls(
                page,
                getattr(page, "url", listing_url) or listing_url,
                detail_re,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 8,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
                scroll_px=getattr(browser, "scroll_px", None) or 2500,
                stale_rounds=getattr(browser, "stale_rounds", None) or 3,
            )
        return urls[:limit]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "geekjob"
        detail_urls = await self.discover(spec, client)
        search_query = self._search_query(spec.url).casefold()
        search_terms = (
            normalize_search_keywords(re.split(r"\s+OR\s+|\s+or\s+", search_query))
            if search_query
            else []
        )
        seen_ids: set[str] = set()
        for detail_url in detail_urls[:limit]:
            id_match = re.search(r"/(?:vacancy/([a-z0-9-]+)|jobs/(\d+))", detail_url)
            final_id = (id_match.group(1) or id_match.group(2)) if id_match else detail_url
            if final_id in seen_ids:
                continue
            seen_ids.add(final_id)
            try:
                response = await safe_fetch(client, detail_url)
            except Exception as exc:
                logger.debug("geekjob.detail_fetch_failed", url=detail_url, error=str(exc))
                continue
            item = _item_from_detail_html(
                str(response.url),
                response.text,
                source_name,
                spec.url,
            )
            if item is not None:
                if search_terms:
                    haystack = item.text.casefold()
                    if not any(
                        all(token in haystack for token in term.casefold().split())
                        for term in search_terms
                    ):
                        continue
                yield item

    @property
    def __name__(self) -> str:
        return "GeekJobParser"
