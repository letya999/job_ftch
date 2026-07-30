"""Parser for Belmeta's mixed-encoding employer vacancy pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.url_scoring import rank_job_urls

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.source_spec import CareerSiteSpec


class BelmetaParser:
    domain_pattern = r"^https?://(?:www\.)?belmeta\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, page_url: str, spec: CareerSiteSpec) -> list[Any]:
        page = LexborHTMLParser(html)
        items: list[Any] = []
        for card in page.css("article.job[data-id]"):
            link = card.css_first("a.job-title[href]")
            desc = card.css_first(".desc")
            if link is None or desc is None:
                continue
            title = " ".join(link.text(strip=True).split())
            description = " ".join(desc.text(separator=" ", strip=True).split())
            external_id = card.attributes.get("data-id")
            href = link.attributes.get("href", "")
            if not (title and description and external_id and href):
                continue
            location_node = card.css_first(".job-data.region")
            company_node = card.css_first(".job-data.company")
            location = (
                " ".join(location_node.text(strip=True).split())
                if location_node is not None
                else None
            )
            company = (
                " ".join(company_node.text(strip=True).split())
                if company_node is not None
                else None
            )
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "belmeta",
                    external_id=external_id,
                    url=urljoin(page_url, href),
                    text=f"{title}\n{description}",
                    metadata={
                        "title": title,
                        "location": location,
                        "company": company,
                        "board_url": spec.url,
                        "parser": "belmeta",
                        "detail_vacancy_confirmed": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[Any]:
        from job_ftch.infrastructure.sources.monitors.dom import discover

        monitor_spec = type("BelmetaSpec", (), {"url": spec.url, "monitor_config": {}})()
        pages = await discover(monitor_spec, client)
        if not isinstance(pages, set):
            return
        limit = spec.limit or 50
        emitted = 0
        for page_url in rank_job_urls(pages, board_url=spec.url):
            response = await client.get(page_url, follow_redirects=True)
            for item in self._items_from_html(response.text, page_url, spec):
                yield item
                emitted += 1
                if emitted >= limit:
                    return


register_site_parser("belmeta", domain_pattern=BelmetaParser.domain_pattern)(BelmetaParser)
