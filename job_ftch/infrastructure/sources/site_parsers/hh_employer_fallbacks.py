"""Company-owned career board repairs."""

from __future__ import annotations

import re
from typing import Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.large_employer_boards import (
    _EmployerBoardParser,
)


class TwoGisCareerParser(_EmployerBoardParser):
    domain_pattern = r"^https?://job\.2gis\.ru/vacancies(?:[/?#]|$)"
    parser_name = "two_gis_career"
    company = "2GIS"
    detail_pattern = re.compile(r"/vacancies/[^/?#]+/(\d+)(?:/)?$")
    supports_discover = False
    supports_search = True

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


register_site_parser("two_gis", domain_pattern=TwoGisCareerParser.domain_pattern)(
    TwoGisCareerParser
)
