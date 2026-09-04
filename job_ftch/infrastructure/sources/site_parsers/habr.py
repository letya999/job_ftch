"""Discover-and-parse parser for career.habr.com vacancy listings."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import structlog
from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
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

_URL_FILTER = r"career\.habr\.com/vacancies/\d+"
_JSONLD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
logger = structlog.get_logger(__name__)


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

    external_id_match = re.search(r"/vacancies/(\d+)", detail_url)
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
            "parser": "site_habr_career",
            "detail_vacancy_confirmed": True,
        },
    )


def _items_from_listing_html(
    html_text: str,
    board_url: str,
    source_name: str,
    keywords: list[str],
) -> list[RawItem]:
    items: list[RawItem] = []
    seen: set[str] = set()
    page = HTMLParser(html_text)
    for link in page.css("a.vacancy-card__title-link"):
        href = str(link.attributes.get("href") or "").strip()
        match = re.search(r"/vacancies/(\d+)", href)
        if match is None:
            continue
        external_id = match.group(1)
        if external_id in seen:
            continue
        seen.add(external_id)
        title = " ".join(link.text(separator=" ", strip=True).split())
        card = link.parent
        while card is not None and "vacancy-card" not in str(card.attributes.get("class") or ""):
            card = card.parent
        extra = ""
        if card is not None:
            extra = " ".join(card.text(separator=" ", strip=True).split())
        text = "\n".join(part for part in (title, extra) if part)
        if not title or not text_matches_keywords(text, keywords):
            continue
        items.append(
            build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=external_id,
                url=f"https://career.habr.com/vacancies/{external_id}",
                text=text,
                metadata={
                    "board_url": board_url,
                    "job_url": f"https://career.habr.com/vacancies/{external_id}",
                    "parser": "site_habr_career",
                    "detail_vacancy_confirmed": False,
                },
            )
        )
    return items


class HabrCareerParser:
    domain_pattern = r"^https?://career\.habr\.com/"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
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
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = getattr(manifest_entry, "detail_pattern", None) or (
            r"((?:https?://career\.habr\.com)?/vacancies/\d+(?:[^\"' <]*)?)"
        )
        return re.compile(str(pattern), re.IGNORECASE)

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

    def _listing_page_url(self, url: str, page: int) -> str:
        if page <= 1:
            return url
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        detail_re = self._detail_re()
        limit = self._limit(spec.limit)
        page_count = self._page_count(limit)
        collected: list[str] = []
        seen: set[str] = set()

        for page in range(1, page_count + 1):
            listing_url = self._listing_page_url(spec.url, page)
            try:
                response = await safe_fetch(client, listing_url)
            except Exception as exc:
                logger.debug("habr.listing_fetch_failed", url=listing_url, error=str(exc))
                break
            current_url = str(response.url)
            if detail_re.search(current_url):
                url = current_url.split("?", 1)[0]
                if url not in seen:
                    seen.add(url)
                    collected.append(url)
                return collected[:limit]
            for url in extract_urls_with_limit(str(response.text), detail_re, current_url, limit):
                if url in seen:
                    continue
                seen.add(url)
                collected.append(url)
                if len(collected) >= limit:
                    return collected[:limit]

        return collected[:limit]

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        limit = self._limit(spec.limit)
        source_name = spec.source_name or "habr_career"
        keywords = keywords_from_spec(spec)
        seen_ids: set[str] = set()
        emitted = 0
        for page in range(1, self._page_count(limit) + 1):
            listing_url = self._listing_page_url(spec.url, page)
            try:
                response = await safe_fetch(client, listing_url)
            except Exception as exc:
                logger.debug("habr.listing_fetch_failed", url=listing_url, error=str(exc))
                return
            for item in _items_from_listing_html(
                str(response.text), spec.url, source_name, keywords
            ):
                item_id = str(item.external_id or item.url or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                yield item
                emitted += 1
                if emitted >= limit:
                    return
            if emitted == 0 and page == 1:
                return

    @property
    def __name__(self) -> str:
        return "HabrCareerParser"


register_site_parser(
    "habr_career",
    domain_pattern=HabrCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:career.habr.com"),
)(HabrCareerParser)
