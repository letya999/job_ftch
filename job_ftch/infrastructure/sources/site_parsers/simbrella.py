"""Extract Simbrella's server-rendered vacancy cards."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://www.simbrella.com/all-vacancies/"


class SimbrellaParser:
    """Turn the public ProcessWire vacancy grid into rich source items."""

    domain_pattern = r"^https?://(?:www\.)?simbrella\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        for card in LexborHTMLParser(html).css(".vacanciees_grid article"):
            title_node = card.css_first("h3")
            link = card.css_first('a[href*="/all-vacancies/"]')
            if title_node is None or link is None:
                continue
            title = " ".join(title_node.text(separator=" ", strip=True).split())
            url = urljoin(_BOARD_URL, link.attributes.get("href", ""))
            if not title or url == _BOARD_URL:
                continue
            external_id = hashlib.sha256(url.encode()).hexdigest()[:20]
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "simbrella",
                    external_id=external_id,
                    url=url,
                    text=title,
                    metadata={"board_url": _BOARD_URL, "parser": "simbrella_ssr"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _BOARD_URL)
        for item in self._items_from_html(response.text, spec)[: spec.limit or 50]:
            yield item


register_site_parser("simbrella", domain_pattern=SimbrellaParser.domain_pattern)(SimbrellaParser)
