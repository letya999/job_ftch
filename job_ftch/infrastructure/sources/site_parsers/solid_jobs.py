"""Browser parser for the public Solid.jobs offer listing."""

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


_BOARD_URL = "https://solid.jobs/offers/it"
_OFFER_PATH = re.compile(r"^/offer/(\d+)/[^/]+$")


class SolidJobsParser:
    """Extract client-rendered public IT offer cards from Solid.jobs."""

    domain_pattern = r"^https?://(?:www\.)?solid\.jobs(?:/|$)"
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
        seen: set[str] = set()
        for link in LexborHTMLParser(html).css('a[href*="/offer/"]'):
            detail_url = urljoin(_BOARD_URL, link.attributes.get("href", "")).split("?", 1)[0]
            match = _OFFER_PATH.fullmatch(detail_url.removeprefix("https://solid.jobs"))
            text = " ".join(link.text(separator=" ", strip=True).split())
            if match is None or not text or detail_url in seen:
                continue
            seen.add(detail_url)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "solid_jobs",
                    external_id=match.group(1),
                    url=detail_url,
                    text=text,
                    metadata={"board_url": _BOARD_URL, "parser": "solid_jobs_browser"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            config, bypass_strategy=spec.monitor_config.get("_bypass_strategy")
        ) as page:
            await navigate(page, _BOARD_URL, config)
            await page.wait_for_timeout(1500)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec)[: spec.limit or 50]:
            yield item


register_site_parser("solid_jobs", domain_pattern=SolidJobsParser.domain_pattern)(SolidJobsParser)
