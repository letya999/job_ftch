"""Browser parser for public job listings on 999.md."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

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


_WORK_URL = "https://999.md/ro/work"
_DETAIL_PATH = re.compile(r"/ro/(\d+)(?:$|[?#])")


class Nine99MdParser:
    """Extract hydrated public work cards without relying on generic URL scoring."""

    domain_pattern = r"^https?://(?:www\.)?999\.md(?:/|$)"
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
        tree = LexborHTMLParser(html)
        for link in tree.css('[data-testid="infinite-ads-home"] a[href]'):
            href = link.attributes.get("href", "")
            detail_url = urljoin(_WORK_URL, href).split("?", 1)[0]
            match = _DETAIL_PATH.search(detail_url)
            title_node = link.css_first("h4")
            title = " ".join((title_node.text(strip=True) if title_node else "").split())
            text = " ".join(link.text(separator=" ", strip=True).split())
            if match is None or not title or len(text) < len(title) + 10:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "999_md_jobs",
                    external_id=match.group(1),
                    url=detail_url,
                    text=text,
                    metadata={"board_url": _WORK_URL, "parser": "999_md_work_browser"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            config, bypass_strategy=spec.monitor_config.get("_bypass_strategy")
        ) as page:
            await navigate(page, _WORK_URL, config)
            await page.wait_for_timeout(3500)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec)[: spec.limit or 50]:
            yield item


register_site_parser("999_md", domain_pattern=Nine99MdParser.domain_pattern)(Nine99MdParser)
