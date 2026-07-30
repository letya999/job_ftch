"""Browser parser for Spitamenbank's inline vacancy accordions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page, safe_content
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import resolve_browser_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class SpitamenParser:
    domain_pattern = r"^https?://(?:www\.)?spitamenbank\.tj(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        for card in LexborHTMLParser(html).css(".sb-item[id]"):
            title_node = card.css_first(".sb-title")
            body_node = card.css_first(".sb-content")
            if title_node is None or body_node is None:
                continue
            title = " ".join(title_node.text(strip=True).split())
            body = " ".join(body_node.text(separator=" ", strip=True).split())
            external_id = card.attributes.get("id", "")
            if not (title and body and external_id):
                continue
            yield_item = build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "spitamenbank",
                external_id=external_id,
                url=f"{spec.url.rstrip('/')}#{external_id}",
                text=f"{title}\n{body}",
                metadata={"board_url": spec.url, "parser": "spitamen_browser"},
            )
            items.append(yield_item)
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            config, bypass_strategy=spec.monitor_config.get("_bypass_strategy")
        ) as page:
            await navigate(page, spec.url, config)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec)[: spec.limit or 50]:
            yield item


register_site_parser("spitamen", domain_pattern=SpitamenParser.domain_pattern)(SpitamenParser)
