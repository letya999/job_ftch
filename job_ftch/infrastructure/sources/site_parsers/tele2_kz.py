"""Use the authoritative HH employer board linked by Tele2 Kazakhstan."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_HH_EMPLOYER_RE = re.compile(
    r"https://(?:[a-z0-9-]+\.)?hh\.kz/employer/\d+[^\"' <]*",
    re.IGNORECASE,
)


def _extract_hh_employer_url(page: str) -> str | None:
    match = _HH_EMPLOYER_RE.search(html.unescape(page).replace(r"\u0026", "&"))
    return match.group(0).rstrip("\\") if match else None


class Tele2KazakhstanParser:
    domain_pattern = r"^https?://job\.tele2\.kz(?:/|$)"
    has_custom_parse = True
    supports_search = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await client.get(spec.url, follow_redirects=True)
        response.raise_for_status()
        employer_url = _extract_hh_employer_url(response.text)
        if employer_url is None:
            raise ValueError("Tele2 Kazakhstan no longer exposes its HH employer board")
        delegated = spec.model_copy(update={"url": employer_url})
        async for item in HhParser().parse(delegated, client):
            yield item

    @property
    def __name__(self) -> str:
        return "Tele2KazakhstanParser"


register_site_parser(
    "tele2_kz",
    domain_pattern=Tele2KazakhstanParser.domain_pattern,
)(Tele2KazakhstanParser)
