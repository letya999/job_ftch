"""Extract inline Ask Wire vacancies from the public careers page."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

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


_CAREERS_URL = "https://ask-wire.com/about-us-careers/"


class AskWireParser:
    """Build items from rich tab panels whose application endpoint is email-only."""

    domain_pattern = r"^https?://(?:www\.)?ask-wire\.com(?:/|$)"
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
        for panel in LexborHTMLParser(html).css(".wp-block-kadence-tabs"):
            title_node = panel.css_first(".kt-title-text strong")
            location_node = panel.css_first(".kt-title-sub-text")
            title = " ".join((title_node.text(strip=True) if title_node else "").split())
            location = " ".join((location_node.text(strip=True) if location_node else "").split())
            text = " ".join(panel.text(separator=" ", strip=True).split())
            if not title or len(text) < len(title) + 50:
                continue
            external_id = hashlib.sha256(f"{title}\n{location}".encode()).hexdigest()[:20]
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "ask_wire",
                    external_id=external_id,
                    url=f"{_CAREERS_URL}#role-{external_id}",
                    text=text,
                    metadata={
                        "board_url": _CAREERS_URL,
                        "location": location or None,
                        "parser": "ask_wire_inline",
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _CAREERS_URL)
        for item in self._items_from_html(response.text, spec)[: spec.limit or 50]:
            yield item


register_site_parser("ask_wire", domain_pattern=AskWireParser.domain_pattern)(AskWireParser)
