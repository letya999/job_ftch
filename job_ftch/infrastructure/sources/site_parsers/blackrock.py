"""Parser for the public BlackRock TalentBrew careers board."""

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


_BOARD_URL = "https://careers.blackrock.com/search-jobs"
_JOB_PATH = re.compile(r"^/job/[^/]+/[^/]+/45831/(\d+)$")


class BlackRockParser:
    """Follow the marketing redirect to BlackRock's public job board."""

    domain_pattern = (
        r"^https?://(?:www\.)?blackrock\.com(?:/|$)|^https?://careers\.blackrock\.com(?:/|$)"
    )
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for link in LexborHTMLParser(html).css('a[href*="/job/"]'):
            detail_url = urljoin(_BOARD_URL, link.attributes.get("href", "")).split("?", 1)[0]
            match = _JOB_PATH.fullmatch(detail_url.removeprefix("https://careers.blackrock.com"))
            title = " ".join(link.text(separator=" ", strip=True).split())
            if match is None or not title or detail_url in seen:
                continue
            seen.add(detail_url)
            parent = link.parent
            text = " ".join(parent.text(separator=" ", strip=True).split()) if parent else title
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "blackrock",
                    external_id=match.group(1),
                    url=detail_url,
                    text=text if len(text) >= len(title) else title,
                    metadata={"board_url": _BOARD_URL, "parser": "blackrock_talentbrew"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _BOARD_URL)
        for item in self._items_from_html(response.text, spec)[: spec.limit or 50]:
            yield item


register_site_parser("blackrock", domain_pattern=BlackRockParser.domain_pattern)(BlackRockParser)
