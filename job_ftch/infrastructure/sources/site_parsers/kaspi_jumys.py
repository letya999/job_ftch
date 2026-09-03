"""SSR parser for the official Kaspi Jumys vacancy board."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    ListingPagination,
    keywords_from_spec,
    paginate_listing,
    safe_fetch,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(r"^/a/[^/?#]+-(\d+)(?:/|$)", re.IGNORECASE)


def _clean(value: str) -> str:
    return " ".join(value.split())


class KaspiJumysParser:
    domain_pattern = r"^https?://jumys\.kaspi\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "kaspi_jumys_ssr"

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        page = LexborHTMLParser(html)
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in page.css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            match = _DETAIL_RE.match(urlsplit(href).path)
            if match is None:
                continue
            parsed_url = urlsplit(urljoin(board_url, href))
            url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
            if url in seen:
                continue
            seen.add(url)
            card = anchor
            for _ in range(4):
                parent = getattr(card, "parent", None)
                if parent is None:
                    break
                card = parent
                if "vacancy-listing-item" in str(card.attributes.get("class") or ""):
                    break
            text = _clean(card.text(separator="\n", strip=True))
            if not text:
                continue
            title = _clean(anchor.text(strip=True)) or text.split(" ", 1)[0]
            if not text_matches_keywords(f"{title}\n{text}\n{url}", keywords):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text="\n".join(part for part in (title, text) if part),
                    metadata={
                        "board_url": board_url,
                        "parser": "kaspi_jumys_ssr",
                        "observation_kind": "vacancy_detail",
                        "detail_vacancy_confirmed": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        keywords = keywords_from_spec(spec)
        source_name = spec.source_name or "kaspi_jumys_ssr"

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
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.url,
        )
        for item in items:
            yield item


register_site_parser(
    "kaspi_jumys",
    domain_pattern=KaspiJumysParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:jumys.kaspi.kz",
        has_stable_id=True,
        has_stable_url=True,
        has_embedded_state=True,
        rationale="Official Kaspi Jumys SSR listing exposes stable vacancy links without sequential browser scraping.",
    ),
)(KaspiJumysParser)
