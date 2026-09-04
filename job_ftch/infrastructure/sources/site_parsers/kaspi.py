"""HTTP listing parser for job.kaspi.kz."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    ListingPagination,
    extract_urls_with_limit,
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    safe_fetch,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class KaspiParser:
    domain_pattern = r"(?:www\.)?job\.kaspi\.kz(?:/|$)"
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
        path = parsed.path or "/search"
        if path.rstrip("/") in {"", "/"}:
            path = "/search"
        listing = urlunparse(parsed._replace(path=path, query=""))
        return [with_query_params(listing, {"search": " OR ".join(terms)})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "kaspi"

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = getattr(manifest_entry, "detail_pattern", None) or r"/vacancy/[a-z0-9-]+"
        return re.compile(str(pattern), re.IGNORECASE)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        detail_re = self._detail_re()
        keywords = keywords_from_spec(spec)
        try:

            async def fetch(url: str) -> str:
                response = await safe_fetch(client, url)
                return str(response.text)

            def extract(html: str, url: str) -> list[str]:
                found = extract_urls_with_limit(html, detail_re, url, max(limit, 50))
                if not keywords:
                    return found
                return [item for item in found if text_matches_keywords(item, keywords)]

            urls = await paginate_listing(
                fetch,
                extract,
                spec.url,
                limit=limit,
                pagination=ListingPagination(),
            )
            return urls[:limit]
        except Exception:  # noqa: BLE001 - empty HTTP listing is a confirmed miss
            return []

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        detail_re = self._detail_re()
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in LexborHTMLParser(html).css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            url = urljoin(board_url, href.split("?", 1)[0])
            match = detail_re.search(url)
            if match is None or url in seen:
                continue
            seen.add(url)
            title = " ".join(anchor.text(separator=" ", strip=True).split())
            if len(title) < 3:
                continue
            if not text_matches_keywords(f"{title}\n{url}", keywords):
                continue
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=slug,
                    url=url,
                    text=title,
                    metadata={
                        "board_url": board_url,
                        "parser": "kaspi",
                        "company": "Kaspi.kz",
                        "company_authoritative": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        limit = spec.limit or 50
        source_name = spec.source_name or "kaspi"

        async def fetch(url: str) -> str:
            response = await safe_fetch(client, url)
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            return self._items_from_html(html, url, source_name, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            spec.url,
            limit=limit,
            pagination=ListingPagination(),
            identity=lambda item: item.url,
        )
        for item in items:
            yield item

    @property
    def __name__(self) -> str:
        return "KaspiParser"


register_site_parser(
    "kaspi",
    domain_pattern=KaspiParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:job.kaspi.kz",
        has_stable_url=True,
        has_embedded_state=True,
        requires_full_snapshot=False,
        rationale="job.kaspi.kz uses a dedicated SPA parser with stable vacancy detail URLs.",
    ),
)(KaspiParser)
