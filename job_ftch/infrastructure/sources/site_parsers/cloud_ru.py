"""HTTP parser for cloud.ru career vacancies."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    ListingPagination,
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(r"/career/vacancies/(\d+)(?:/)?$", re.IGNORECASE)


class CloudRuCareerParser:
    domain_pattern = r"^https?://(?:www\.)?cloud\.ru/career(?:/|$)"
    has_custom_parse = True
    supports_discover = False
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
        listing = base_url.split("?", 1)[0] or "https://cloud.ru/career/vacancies"
        if "/career/vacancies" not in listing:
            listing = urljoin(listing, "/career/vacancies")
        return [with_query_params(listing, {"search": term}) for term in terms]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return "cloud_ru"

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        page = LexborHTMLParser(html)
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in page.css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            url = urljoin(board_url, href.split("?", 1)[0])
            match = _DETAIL_RE.search(url)
            if match is None or url in seen:
                continue
            seen.add(url)
            title = " ".join(anchor.text(separator=" ", strip=True).split())
            if len(title) < 3:
                continue
            if not text_matches_keywords(f"{title}\n{url}", keywords):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=title,
                    metadata={
                        "board_url": board_url,
                        "parser": "cloud_ru",
                        "company": "Cloud.ru",
                        "company_authoritative": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or "cloud_ru"

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            return self._items_from_html(html, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            spec.url,
            limit=limit,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        for item in items:
            yield item


register_site_parser(
    "cloud_ru",
    domain_pattern=CloudRuCareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:cloud.ru"),
)(CloudRuCareerParser)
