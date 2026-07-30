"""Route Nokia's marketing careers page to the public Oracle job board."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.monitors.sitemap import discover as discover_sitemap
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://jobs.nokia.com/en/sites/CX_1"
_DETAIL_URL = re.compile(r"^https://jobs\.nokia\.com/[a-z]{2}/job/\d+/?$", re.I)


class NokiaParser:
    domain_pattern = r"^https?://(?:www\.)?nokia\.com/(?:about-us/)?careers(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        board_spec = spec.model_copy(update={"url": _BOARD_URL})
        urls, _ = await discover_sitemap(board_spec, client)
        return sorted(url for url in urls if _DETAIL_URL.match(url))[: spec.limit or 50]


register_site_parser("nokia", domain_pattern=NokiaParser.domain_pattern)(NokiaParser)
