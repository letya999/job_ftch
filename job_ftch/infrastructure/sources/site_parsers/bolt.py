"""Extract Bolt's server-rendered inline careers accordions."""

from __future__ import annotations

import re
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


_POSITIONS_URL = "https://bolt.eu/en/careers/positions/"
_DETAIL_PATH = re.compile(r"/en/careers/positions/([0-9a-f-]{36})/?$")


class BoltParser:
    """Use rich SSR role cards instead of invoking a browser for Bolt."""

    domain_pattern = r"^https?://(?:www\.)?bolt\.eu(?:/|$)"
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
        for card in LexborHTMLParser(html).css('[data-testid="AccordionItem"]'):
            link = next(
                (
                    anchor
                    for anchor in card.css("a[href]")
                    if _DETAIL_PATH.search(
                        urljoin(_POSITIONS_URL, anchor.attributes.get("href", ""))
                    )
                ),
                None,
            )
            title_node = card.css_first("h3 span")
            if link is None or title_node is None:
                continue
            detail_url = urljoin(_POSITIONS_URL, link.attributes.get("href", ""))
            match = _DETAIL_PATH.search(detail_url)
            title = " ".join(title_node.text(strip=True).split())
            text = " ".join(card.text(separator=" ", strip=True).split())
            if match is None or not title or len(text) < len(title) + 10:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "bolt",
                    external_id=match.group(1),
                    url=detail_url,
                    text=text,
                    metadata={"board_url": _POSITIONS_URL, "parser": "bolt_ssr"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _POSITIONS_URL)
        for item in self._items_from_html(response.text, spec)[: spec.limit or 50]:
            yield item


register_site_parser("bolt", domain_pattern=BoltParser.domain_pattern)(BoltParser)
