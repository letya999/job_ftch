"""Discover public vacancy detail pages on Lucky Hunter's jobs listing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.monitors.api_sniffer import discover as discover_api_sniffer
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://luckyhunter.co.uk/jobs"


class LuckyHunterParser:
    domain_pattern = r"^https?://(?:www\.)?luckyhunter\.co\.uk(?:/|$)"
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
        result = await discover_api_sniffer(board_spec, client)
        return sorted(result.urls)[: spec.limit or 50]


register_site_parser("luckyhunter", domain_pattern=LuckyHunterParser.domain_pattern)(
    LuckyHunterParser
)
