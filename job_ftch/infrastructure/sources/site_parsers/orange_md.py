"""Browser parser for Orange Mnewova's embedded eRecruiter vacancy board."""

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


_VACANCIES_URL = "https://www.orange.md/ro/cariera/functii-vacante"


class OrangeMdParser:
    """Extract public eRecruiter result rows rendered inside Orange Mnewova."""

    domain_pattern = r"^https?://(?:www\.)?orange\.md(?:/|$)"
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
        for row in LexborHTMLParser(html).css("tr[skkresult='offer'][jobofferid]"):
            cells = row.css("td")
            if not cells:
                continue
            title = " ".join(cells[0].text(strip=True).split())
            location = " ".join(cells[1].text(strip=True).split()) if len(cells) > 1 else ""
            department = " ".join(cells[2].text(strip=True).split()) if len(cells) > 2 else ""
            external_id = row.attributes.get("jobofferid", "")
            if not (title and external_id):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "orange_mnewova",
                    external_id=external_id,
                    url=f"{_VACANCIES_URL}#job-{external_id}",
                    text="\n".join(part for part in (title, location, department) if part),
                    metadata={
                        "board_url": _VACANCIES_URL,
                        "location": location or None,
                        "department": department or None,
                        "parser": "orange_md_erecruiter_browser",
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
            await navigate(page, _VACANCIES_URL, config)
            items: list[RawItem] = []
            for _ in range(3):
                await page.wait_for_timeout(3000)
                items = self._items_from_html(await safe_content(page), spec)
                if items:
                    break
        for item in items[: spec.limit or 50]:
            yield item


register_site_parser("orange_md", domain_pattern=OrangeMdParser.domain_pattern)(OrangeMdParser)
