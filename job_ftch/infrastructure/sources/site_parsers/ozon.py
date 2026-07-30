"""Site parser for ozon.tech/vacancies.

SPA — needs a real browser. Provide `render=True` and `wait="domcontentloaded"`
runtime defaults so the default monitor chain knows to spin up Playwright.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_URL_FILTER = r"ozon\.tech/vacancies/[a-f0-9\-]+-[a-z0-9-]+/?$"


class OzonTechParser:
    domain_pattern = r"^https?://ozon\.tech/vacancies"
    has_custom_parse = False  # runtime-defaults only

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        del spec, client
        return
        yield  # pragma: no cover

    @property
    def __name__(self) -> str:
        return "OzonTechParser"


register_site_parser(
    "ozon_tech",
    domain_pattern=OzonTechParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:ozon.tech"),
)(OzonTechParser)
