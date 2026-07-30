"""Parser for Rabota.md's server-rendered vacancy cards."""

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


class RabotaMdParser:
    domain_pattern = r"^https?://(?:www\.)?rabota\.md(?:/|$)"
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
        for card in LexborHTMLParser(html).css("[data-vacancyid]"):
            link = card.css_first('a[href*="/locuri-de-munca/"]')
            if link is None:
                continue
            external_id = card.attributes.get("data-vacancyid")
            href = link.attributes.get("href", "")
            title = " ".join(link.text(separator=" ", strip=True).split())
            body = " ".join(card.text(separator=" ", strip=True).split())
            if not (external_id and href and title and len(body) >= len(title) + 20):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "rabota_md",
                    external_id=external_id,
                    url=urljoin(page_url, href),
                    text=f"{title}\n{body}",
                    metadata={
                        "board_url": spec.url,
                        "parser": "rabota_md",
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


register_site_parser("rabota_md", domain_pattern=RabotaMdParser.domain_pattern)(RabotaMdParser)
