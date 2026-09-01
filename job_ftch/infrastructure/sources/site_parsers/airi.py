"""Runtime defaults for the AIRI research institute career board."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.career_site import client_for_config
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import _parse_detail_board

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


@register_site_parser(
    "airi",
    domain_pattern=r"^https?://(?:www\.)?airi\.net(?:/|$)",
)
class AiriCareerParser:
    """Use AIRI's server-rendered vacancy pages with scoped TLS relaxation."""

    domain_pattern = r"^https?://(?:www\.)?airi\.net(?:/|$)"
    has_custom_parse = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
            url_filter=r"airi\.net/ru/hr/[^/?#]+_\d+/?$",
            extra={"force_monitor": "dom", "skip_ssl": True},
        )

    def parser_kind(self, url: str) -> None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        async with client_for_config(client, {"skip_ssl": True}) as insecure_client:
            async for item in _parse_detail_board(
                spec,
                insecure_client,
                href_pattern=re.compile(r"/ru/hr/([^/?#]+_\d+)/?$"),
                parser_name="airi",
                company="AIRI",
            ):
                yield item
