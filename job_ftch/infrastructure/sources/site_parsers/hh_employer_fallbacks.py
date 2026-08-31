"""Official HH employer-board fallbacks for unreachable company career sites."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class _HhEmployerFallback:
    has_custom_parse = True
    supports_discover = False
    employer_url: str

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        delegated = spec.model_copy(update={"url": self.employer_url})
        async for item in HhParser().parse(delegated, client):
            yield item


class TwoGisHhFallback(_HhEmployerFallback):
    domain_pattern = r"^https?://job\.2gis\.ru/vacancies(?:[/?#]|$)"
    employer_url = "https://hh.ru/employer/64174"


class AlfaBankHhFallback(_HhEmployerFallback):
    domain_pattern = r"^https?://job\.alfabank\.ru/vacancies/digital(?:[/?#]|$)"
    employer_url = "https://hh.ru/employer/80"


for _name, _parser in (
    ("two_gis_hh", TwoGisHhFallback),
    ("alfa_bank_hh", AlfaBankHhFallback),
):
    register_site_parser(_name, domain_pattern=_parser.domain_pattern)(_parser)
