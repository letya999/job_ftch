"""Parser for Qyzmet's server-rendered vacancy cards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class QyzmetParser:
    domain_pattern = r"^https?://(?:www\.)?qyzmet\.kz(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, page_url: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        for card in LexborHTMLParser(html).css("article.job[data-id]"):
            link = card.css_first('a.job-title[href*="/jobdesc?id="]')
            description = card.css_first(".desc")
            if link is None or description is None:
                continue
            title = " ".join(link.text(strip=True).split())
            body = " ".join(description.text(separator=" ", strip=True).split())
            external_id = card.attributes.get("data-id")
            href = link.attributes.get("href", "")
            if not (title and body and external_id and href):
                continue
            company_node = card.css_first(".job-data.company")
            location_node = card.css_first(".job-data.region")
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "qyzmet",
                    external_id=external_id,
                    url=urljoin(page_url, href),
                    text=f"{title}\n{body}",
                    metadata={
                        "board_url": spec.url,
                        "company": (
                            " ".join(company_node.text(strip=True).split())
                            if company_node is not None
                            else None
                        ),
                        "location": (
                            " ".join(location_node.text(strip=True).split())
                            if location_node is not None
                            else None
                        ),
                        "parser": "qyzmet",
                        "detail_vacancy_confirmed": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await client.get(spec.url, follow_redirects=True)
        response.raise_for_status()
        for item in self._items_from_html(response.text, str(response.url), spec)[
            : spec.limit or 50
        ]:
            yield item


register_site_parser("qyzmet", domain_pattern=QyzmetParser.domain_pattern)(QyzmetParser)
