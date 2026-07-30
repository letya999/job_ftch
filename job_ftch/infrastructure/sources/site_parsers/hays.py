"""Route Hays Poland root to its public job-search listing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://www.hays.pl/en/job-search"


class HaysParser:
    domain_pattern = r"^https?://(?:www\.)?hays\.pl(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, _BOARD_URL)
        urls = {
            urljoin(_BOARD_URL, anchor.attributes.get("href", "")).split("#", 1)[0]
            for anchor in LexborHTMLParser(response.text).css('a[href*="/job-detail/"]')
        }
        return sorted(urls)[: spec.limit or 50]


register_site_parser("hays", domain_pattern=HaysParser.domain_pattern)(HaysParser)
