"""HTTP listing parser for BCC career vacancies."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

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
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(r"/career/(\d+)(?:/)?$", re.IGNORECASE)
_LISTING_PATH = "/career/vacancies/"


class BccCareerParser:
    domain_pattern = r"^https?://(?:www\.)?bcc\.kz(?:/|$)"
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
        return [urlunparse(parsed._replace(path=_LISTING_PATH, query=""))]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"bcc\.kz/career/\d+",
            render=False,
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "bcc_career"

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
            parent_text = ""
            if anchor.parent is not None:
                parent_text = " ".join(anchor.parent.text(separator=" ", strip=True).split())
            text = "\n".join(part for part in (title, parent_text) if part)
            if len(text) < 3:
                continue
            if not text_matches_keywords(text, keywords):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=text,
                    metadata={
                        "board_url": board_url,
                        "parser": "bcc_career",
                        "company": "Банк ЦентрКредит",
                        "company_authoritative": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        listing = spec.url
        if "/career/vacancies" not in listing:
            listing = urljoin(listing, _LISTING_PATH)
        source_name = spec.source_name or "bcc"

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


register_site_parser("bcc_career", domain_pattern=BccCareerParser.domain_pattern)(BccCareerParser)
