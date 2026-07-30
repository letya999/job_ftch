"""Browser-rendered parser for Kapital Bank's Bir careers SPA."""

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


_DETAIL_RE = re.compile(r"/vacancies/(\d+)/?$")


class KapitalBirParser:
    domain_pattern = r"^https?://careers\.bir\.az(?:/|$)"
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
    def _items_from_html(html: str, page_url: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        for link in LexborHTMLParser(html).css('a[href*="/vacancies/"]'):
            href = link.attributes.get("href", "")
            absolute = urljoin(page_url, href)
            match = _DETAIL_RE.search(absolute)
            if match is None:
                continue
            title_node = link.css_first("h3")
            title = " ".join((title_node.text(strip=True) if title_node else "").split())
            text = " ".join(link.text(separator=" ", strip=True).split())
            if not title or len(text) < len(title) + 20:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "kapital_bir",
                    external_id=match.group(1),
                    url=absolute,
                    text=f"{title}\n{text}",
                    metadata={
                        "board_url": spec.url,
                        "parser": "kapital_bir_browser",
                        "detail_vacancy_confirmed": True,
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
            await page.wait_for_timeout(2000)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec.url, spec)[: spec.limit or 50]:
            yield item


register_site_parser("kapital_bir", domain_pattern=KapitalBirParser.domain_pattern)(
    KapitalBirParser
)
