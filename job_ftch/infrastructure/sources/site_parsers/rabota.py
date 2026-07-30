"""Site parser for rabota.by listing pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.hh import HhParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_URL_FILTER = r"(?:[a-z0-9-]+\.)?rabota\.by/vacancy/\d+"


class RabotaByParser:
    domain_pattern = r"^https?://([a-z0-9-]+\.)?rabota\.by(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(url_filter=_URL_FILTER)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        normalized_spec = spec
        if spec.url.rstrip("/") == "https://rabota.by":
            # The unscoped listing can be geolocated to hh.ru.  Belarus area 16
            # keeps vacancy detail URLs on rabota.by, which this parser owns.
            normalized_spec = spec.model_copy(
                update={"url": "https://rabota.by/search/vacancy?area=16"}
            )
        parser = HhParser()
        async for item in parser.parse(normalized_spec, client):
            yield item

    @property
    def __name__(self) -> str:
        return "RabotaByParser"


register_site_parser(
    "rabota_by",
    domain_pattern=RabotaByParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:rabota.by",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=True,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="rabota.by follows the same vacancy-listing and detail-page shape as the HeadHunter family.",
    ),
)(RabotaByParser)
