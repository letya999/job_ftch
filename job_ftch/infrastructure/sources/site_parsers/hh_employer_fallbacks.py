"""Company-owned career board repairs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    _EmployerBoardParser,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class TwoGisCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://job\.2gis\.ru/vacancies(?:[/?#]|$)"
    parser_name = "two_gis_career"
    company = "2GIS"
    detail_pattern = re.compile(r"/vacancies/[^/?#]+/(\d+)(?:/)?$")
    supports_discover = False
    supports_search = False
    terminal_on_error = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            extra={"proxy_rescue_allow_domains": ["job.2gis.ru"]},
        )

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del base_url, keywords, limit
        return []

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        # 2GIS exposes stable vacancy cards in the listing, while detail URLs
        # intermittently return a CDN 403.  Listing text is still useful
        # evidence; do not discard the whole source on an optional detail hit.
        from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
            _parse_detail_board,
        )

        async for item in _parse_detail_board(
            spec,
            client,
            href_pattern=self.detail_pattern,
            parser_name=self.parser_name,
            company=self.company,
            fetch_details=False,
        ):
            yield item


register_site_parser("two_gis", domain_pattern=TwoGisCareerParser.domain_pattern)(
    TwoGisCareerParser
)
