"""Parser for the rendered-in-HTML vacancy cards at astanahub.com."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class AstanaHubParser:
    domain_pattern = r"^https?://(?:www\.)?astanahub\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await client.get(spec.url, follow_redirects=True)
        page = LexborHTMLParser(response.text)
        for card in page.css(".vacancy-item")[: spec.limit or 50]:
            text = "\n".join(
                part.strip()
                for part in card.text(separator="\n", strip=True).splitlines()
                if part.strip()
            )
            onclick = card.attributes.get("onclick", "")
            match = re.search(r"https?://[^'\"]+/vacancy/(\d+)", onclick or "")
            if not match or len(text) < 20:
                continue
            url = match.group(0)
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "astanahub",
                external_id=match.group(1),
                url=url,
                text=text,
                metadata={"board_url": spec.url, "parser": "astanahub"},
            )


register_site_parser("astanahub", domain_pattern=AstanaHubParser.domain_pattern)(AstanaHubParser)
