"""HTTP listing parser for GorodRabот boards (.by / .kz / .ru)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

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
    safe_fetch,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_DETAIL_RE = re.compile(r"/advert/\d+/[a-z0-9_\-]+/?$", re.IGNORECASE)
_URL_FILTER = r"gorodrabot\.(?:by|kz|ru)/advert/\d+/"


class GorodRabotParser:
    domain_pattern = r"^https?://(?:[a-z-]+\.)?gorodrabot\.(?:by|kz|ru)(?:/|$)"
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
        # Role-slug and advanced_search paths 404. Walk the homepage listing
        # and filter titles locally from profile roles.
        return [urlunparse(parsed._replace(path="/", query=""))]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=False,
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "gorodrabot"

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        base_url = str(response.url)
        seen: set[str] = set()
        urls: list[str] = []
        for anchor in HTMLParser(response.text).css("a[href]"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            url = urljoin(base_url, href.split("?", 1)[0])
            if not _DETAIL_RE.search(url) or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= (spec.limit or 50):
                break
        return urls

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in HTMLParser(html).css("a[href]"):
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
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            advert_id = re.search(r"/advert/(\d+)/", url)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=advert_id.group(1) if advert_id else slug,
                    url=url,
                    text=title,
                    metadata={
                        "board_url": board_url,
                        "parser": "gorodrabot",
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        source_name = spec.source_name or "gorodrabot"

        async def fetch(url: str) -> str:
            response = await safe_fetch(client, url)
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            return self._items_from_html(html, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            spec.url,
            limit=spec.limit or 50,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        for item in items:
            yield item


register_site_parser("gorodrabot", domain_pattern=GorodRabotParser.domain_pattern)(GorodRabotParser)
