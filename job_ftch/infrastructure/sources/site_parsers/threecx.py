"""Extract 3CX openings from the public Odoo iframe board."""

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


_BOARD_URL = "https://www.3cx.com/jobs/postings"
_DETAIL_PATH = re.compile(r"/jobs/([a-z0-9-]+-\d+)/?$")


class ThreeCxParser:
    """Follow the 3CX careers iframe and extract its Odoo vacancy cards."""

    domain_pattern = r"^https?://(?:www\.)?3cx\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, board_url: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        for link in LexborHTMLParser(html).css("#jobs_grid a[href]"):
            detail_url = urljoin(board_url, link.attributes.get("href", ""))
            match = _DETAIL_PATH.search(detail_url)
            title_node = link.css_first("h3")
            title = " ".join((title_node.text(strip=True) if title_node else "").split())
            text = " ".join(link.text(separator=" ", strip=True).split())
            if match is None or not title or len(text) < len(title) + 10:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "3cx",
                    external_id=match.group(1),
                    url=detail_url,
                    text=text,
                    metadata={"board_url": board_url, "parser": "3cx_odoo"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _BOARD_URL)
        for item in self._items_from_html(response.text, str(response.url), spec)[
            : spec.limit or 50
        ]:
            yield item


register_site_parser("3cx", domain_pattern=ThreeCxParser.domain_pattern)(ThreeCxParser)
