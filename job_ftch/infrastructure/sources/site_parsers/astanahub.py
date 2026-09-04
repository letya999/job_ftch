"""Parser for the rendered-in-HTML vacancy cards at astanahub.com."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
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


class AstanaHubParser:
    domain_pattern = r"^https?://(?:www\.)?astanahub\.com(?:/|$)"
    has_custom_parse = True
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
        path = parsed.path or "/en/vacancy/"
        if "/vacancy" not in path:
            path = "/en/vacancy/"
        listing = urlunparse(parsed._replace(path=path, query=""))
        # Full role phrases in ``q=`` return zero live cards. Keep the opened
        # listing and filter titles locally from profile roles.
        return [with_query_params(listing, {"opened": "True"})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        page = LexborHTMLParser(html)
        items: list[RawItem] = []
        for card in page.css(".vacancy-item"):
            text = "\n".join(
                part.strip()
                for part in card.text(separator="\n", strip=True).splitlines()
                if part.strip()
            )
            onclick = card.attributes.get("onclick", "")
            match = re.search(r"https?://[^'\"]+/vacancy/(\d+)", onclick or "")
            if not match or len(text) < 20:
                continue
            if not text_matches_keywords(text, keywords):
                continue
            url = match.group(0)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=text,
                    metadata={"board_url": board_url, "parser": "astanahub"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or "astanahub"

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
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


register_site_parser("astanahub", domain_pattern=AstanaHubParser.domain_pattern)(AstanaHubParser)
