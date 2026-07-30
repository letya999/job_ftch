"""Route Sabre's marketing careers page to its public Workday board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.monitors.workday import discover as discover_workday
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://sabre.wd1.myworkdayjobs.com/SabreJobs"


class SabreParser:
    domain_pattern = r"^https?://(?:www\.)?sabre\.com(?:/|$)"
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
        result = await discover_workday(board_spec, client)
        return sorted(result.urls if hasattr(result, "urls") else result)[: spec.limit or 50]


register_site_parser("sabre", domain_pattern=SabreParser.domain_pattern)(SabreParser)
