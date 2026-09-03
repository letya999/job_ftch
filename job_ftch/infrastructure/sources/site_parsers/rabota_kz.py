"""HTTP listing parser for rabota.kz SPA cards."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

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

_DETAIL_RE = re.compile(r"/job/list/([a-f0-9]{16,})(?:/)?$", re.IGNORECASE)
_LISTING = "https://rabota.kz/job/list"


class RabotaKzParser:
    domain_pattern = r"^https?://(?:www\.)?rabota\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        return [with_query_params(_LISTING, {"search": " OR ".join(terms)})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"rabota\.kz/job/list/[a-f0-9]+",
            render=False,
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "rabota_kz"

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in LexborHTMLParser(html).css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            url = urljoin(board_url, href.split("?", 1)[0])
            match = _DETAIL_RE.search(urlparse(url).path)
            if match is None or url in seen:
                continue
            seen.add(url)
            title = " ".join(anchor.text(separator=" ", strip=True).split())
            if len(title) < 3 or title.casefold() in {"развернуть", "скрыть"}:
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
                    metadata={"board_url": board_url, "parser": "rabota_kz"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        listing = spec.url
        if "/job/list" not in listing:
            listing = _LISTING
        source_name = spec.source_name or "rabota_kz"

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            return self._items_from_html(html, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            listing,
            limit=spec.limit or 50,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        for item in items:
            yield item


register_site_parser("rabota_kz", domain_pattern=RabotaKzParser.domain_pattern)(RabotaKzParser)
