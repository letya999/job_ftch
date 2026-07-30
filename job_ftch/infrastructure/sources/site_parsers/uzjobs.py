"""Fast listing parser for uzjobs.uz's legacy Windows-1251 board."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class UzjobsParser:
    domain_pattern = r"^https?://(?:www\.)?uzjobs\.uz(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        listing = await client.get(spec.url, follow_redirects=True)
        page = LexborHTMLParser(listing.text)
        links = []
        for link in page.css('a.link_blue[href*="vakansy_view-"]'):
            href = link.attributes.get("href", "")
            title = " ".join(link.text(separator=" ", strip=True).split())
            if href and title:
                links.append((urljoin(spec.url, href), title))
        seen: set[str] = set()
        unique_links: list[tuple[str, str]] = []
        for url, title in links:
            if url in seen:
                continue
            seen.add(url)
            unique_links.append((url, title))
        links = unique_links
        limit = spec.limit or 50

        async def fetch_detail(url: str, title: str) -> RawItem | None:
            response = await client.get(url, follow_redirects=True)
            detail = LexborHTMLParser(response.text).css_first(".div_main")
            text = "\n".join(
                (detail.text(separator="\n", strip=True) if detail else title).splitlines()
            )
            if len(text) < 20:
                return None
            return build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "uzjobs",
                external_id=url.rsplit("-", 1)[-1].removesuffix(".html"),
                url=url,
                text=f"{title}\n{text}",
                metadata={"board_url": spec.url, "parser": "uzjobs"},
            )

        for item in await asyncio.gather(
            *(fetch_detail(url, title) for url, title in links[:limit])
        ):
            if item is not None:
                yield item


register_site_parser("uzjobs", domain_pattern=UzjobsParser.domain_pattern)(UzjobsParser)
