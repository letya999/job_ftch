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
from job_ftch.infrastructure.sources.http_retry import fetch_with_retry
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    extract_urls_with_limit,
    keywords_from_spec,
    normalize_search_keywords,
    safe_fetch,
    text_matches_keywords,
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


def _plain(value: object) -> str:
    if isinstance(value, dict):
        return _plain(value.get("name") or value.get("title") or value.get("value"))
    if value is None:
        return ""
    return " ".join(str(value).split())


def _item_from_api_row(row: dict[str, Any], source_name: str, board_url: str) -> RawItem | None:
    vacancy_id = row.get("id")
    title = _plain(row.get("position") or row.get("title") or row.get("name"))
    if not isinstance(vacancy_id, (str, int)) or not str(vacancy_id).strip() or not title:
        return None
    external_id = str(vacancy_id).strip()
    detail_url = urljoin("https://geekjob.ru/", f"/vacancy/{external_id}")
    company_name = _plain(row.get("company"))
    locations = [part for part in (_plain(row.get("city")), _plain(row.get("country"))) if part]
    text = "\n".join(
        part
        for part in (
            title,
            company_name,
            ", ".join(locations),
            _plain(row.get("salary")),
            _plain(row.get("jobFormat")),
        )
        if part
    )
    return build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name=source_name,
        external_id=external_id,
        url=detail_url,
        text=text,
        metadata={
            "board_url": board_url,
            "job_url": detail_url,
            "company": company_name or None,
            "locations": locations or None,
            "parser": "site_geekjob",
            "detail_vacancy_confirmed": False,
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
    supports_discover = False
    supports_search = True
    search_mode = "per_keyword"
    confirmed_empty_on_empty = True

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
        limit = self._limit(spec.limit)
        urls: list[str] = []
        seen: set[str] = set()
        for page in range(1, self._page_count(limit) + 1):
            response = await fetch_with_retry(
                client,
                api_url,
                params={"page": str(page), "qs": query},
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            new_on_page = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                vacancy_id = row.get("id")
                if not isinstance(vacancy_id, (str, int)) or not str(vacancy_id).strip():
                    continue
                url = urljoin(spec.url, f"/vacancy/{str(vacancy_id).strip()}")
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
                new_on_page += 1
                if len(urls) >= limit:
                    return urls
            if new_on_page == 0:
                break
        return urls

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"geekjob\.ru/(?:vacancy/[a-z0-9-]+/?|jobs/\d+/?$)",
            render=False,
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
            return []

        page_count = self._page_count(limit)
        collected: list[str] = []
        listing_url = spec.url

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

        return collected[:limit]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "geekjob"
        keywords = keywords_from_spec(spec)
        query = self._search_query(spec.url)
        api_url = urljoin(spec.url, "/json/find/vacancy")
        seen_ids: set[str] = set()
        emitted = 0
        for page in range(1, self._page_count(limit) + 1):
            params: dict[str, str] = {"page": str(page)}
            if query:
                params["qs"] = query
            try:
                response = await fetch_with_retry(
                    client, api_url, params=params, follow_redirects=True
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - empty JSON is a confirmed miss
                logger.info("geekjob.search_api_failed", url=spec.url, error=str(exc))
                return
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                return
            new_on_page = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = _item_from_api_row(row, source_name, spec.url)
                item_id = str(item.external_id or item.url or "") if item else ""
                if not item or not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                new_on_page += 1
                if not text_matches_keywords(item.text, keywords):
                    continue
                yield item
                emitted += 1
                if emitted >= limit:
                    return
            if new_on_page == 0:
                return

    @property
    def __name__(self) -> str:
        return "GeekJobParser"
