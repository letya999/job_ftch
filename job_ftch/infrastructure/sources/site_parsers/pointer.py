"""Browser parser for Pointer's public inline vacancy cards."""

from __future__ import annotations

import hashlib
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


class PointerParser:
    domain_pattern = r"^https?://jobs\.pointer\.ge(?:/|$)"
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
        for card in LexborHTMLParser(html).css("app-vacany .card"):
            title_node = card.css_first(".card-title")
            location_node = card.css_first(".pin-block")
            if title_node is None:
                continue
            title = " ".join(title_node.text(strip=True).split())
            location = " ".join(location_node.text(strip=True).split()) if location_node else ""
            if not title:
                continue
            external_id = hashlib.sha256(f"{title}\n{location}".encode()).hexdigest()[:20]
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "pointer",
                    external_id=external_id,
                    url=f"{spec.url.rstrip('/')}#vacancy-{external_id}",
                    text="\n".join(part for part in (title, location) if part),
                    metadata={
                        "board_url": spec.url,
                        "location": location or None,
                        "parser": "pointer_browser",
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            config, bypass_strategy=spec.monitor_config.get("_bypass_strategy")
        ) as page:
            await navigate(page, spec.url, config)
            await page.wait_for_timeout(1000)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec)[: spec.limit or 50]:
            yield item


register_site_parser("pointer", domain_pattern=PointerParser.domain_pattern)(PointerParser)
