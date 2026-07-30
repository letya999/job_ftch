"""Discover PwC CEE Workday postings embedded by the Phenom landing page."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_SEARCH_URL = "https://jobs-cee.pwc.com/ce/en/search-results"
_WORKDAY_JOB = re.compile(r"https://pwc\.wd3\.myworkdayjobs\.com/[^\"'< >]+/job/[^\"'< >]+", re.I)


class PwcParser:
    """Expose actual public Workday postings instead of Phenom page chrome."""

    domain_pattern = r"^https?://(?:www\.)?pwc\.pl(?:/|$)|^https?://jobs-cee\.pwc\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, _SEARCH_URL)
        seen: set[str] = set()
        urls: list[str] = []
        for match in _WORKDAY_JOB.finditer(response.text):
            url = match.group(0).removesuffix("/apply")
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= (spec.limit or 50):
                break
        return urls


register_site_parser("pwc", domain_pattern=PwcParser.domain_pattern)(PwcParser)
