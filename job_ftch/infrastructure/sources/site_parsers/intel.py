"""Route retired Intel location pages to the official public Workday board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.monitors.workday import discover as discover_workday
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://intel.wd1.myworkdayjobs.com/External"


class IntelParser:
    domain_pattern = r"^https?://(?:www\.)?intel\.com/content/www/.*/jobs/(?:locations/)?"
    has_custom_parse = True
    supports_discover = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        location_slug = spec.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html").casefold()
        board_spec = spec.model_copy(update={"url": _BOARD_URL, "limit": 100})
        result = await discover_workday(board_spec, client)
        urls = result.urls if hasattr(result, "urls") else result
        return sorted(url for url in urls if location_slug in url.casefold())[: spec.limit or 50]


register_site_parser("intel", domain_pattern=IntelParser.domain_pattern)(IntelParser)
