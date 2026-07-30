"""Read Talentuch vacancies from its linked public Breezy board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.breezy import BreezyParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://talentuch.breezy.hr/"


class TalentuchParser:
    domain_pattern = r"^https?://(?:www\.)?talentuch\.com(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        board_spec = spec.model_copy(update={"url": _BOARD_URL})
        async for item in BreezyParser().parse(board_spec, client):  # type: ignore[abstract]
            yield item.model_copy(update={"source_name": spec.source_name or "Talentuch"})


register_site_parser("talentuch", domain_pattern=TalentuchParser.domain_pattern)(TalentuchParser)
