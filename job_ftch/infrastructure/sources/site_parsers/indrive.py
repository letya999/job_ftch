"""HTTP listing parser for inDrive careers (Pinpoint-backed WP cards)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    keywords_from_spec,
    normalize_search_keywords,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_LISTING = "https://careers.indrive.com/vacancies/"


class InDriveCareerParser:
    domain_pattern = r"^https?://careers\.indrive\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        parsed = urlparse(base_url)
        path = parsed.path or "/vacancies/"
        if "/vacancies" not in path:
            path = "/vacancies/"
        return [urlunparse(parsed._replace(path=path, query="", fragment=""))]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"careers\.indrive\.com/vacancies/[a-f0-9]{16,}",
            render=False,
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "indrive"

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for card in LexborHTMLParser(html).css("a.c-job-card[data-id]"):
            external_id = str(card.attributes.get("data-id") or "").strip()
            data_url = str(card.attributes.get("data-url") or "").strip()
            href = str(card.attributes.get("href") or "").strip()
            url = urljoin(board_url, data_url or href)
            if not external_id or not url or url in seen:
                continue
            seen.add(url)
            title = " ".join(card.text(separator=" ", strip=True).split())
            if len(title) < 3:
                continue
            if not text_matches_keywords(title, keywords):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=external_id,
                    url=url,
                    text=title,
                    metadata={
                        "board_url": board_url,
                        "parser": "indrive",
                        "company": "inDrive",
                        "company_authoritative": True,
                    },
                )
            )
        return items

    @staticmethod
    def _page_url(listing: str, page: int) -> str:
        if page <= 1:
            return listing
        return urljoin(listing.rstrip("/") + "/", f"page/{page}/")

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        listing = spec.url if "/vacancies" in spec.url else _LISTING
        source_name = spec.source_name or "indrive"
        limit = spec.limit or 50
        seen: set[str] = set()
        emitted = 0
        for page in range(1, DEFAULT_LISTING_MAX_PAGES + 1):
            response = await client.get(self._page_url(listing, page), follow_redirects=True)
            response.raise_for_status()
            new_on_page = 0
            for item in self._items_from_html(response.text, spec.url, source_name, keywords):
                key = str(item.external_id or item.url or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                new_on_page += 1
                yield item
                emitted += 1
                if emitted >= limit:
                    return
            if new_on_page == 0:
                return


register_site_parser("indrive", domain_pattern=InDriveCareerParser.domain_pattern)(
    InDriveCareerParser
)
