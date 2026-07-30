"""Extract Humo Bank vacancies embedded in Next.js RSC page data."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://humo.tj/tj/vacancies/all"
_VACANCY = re.compile(
    r'jobTitle\\?"\s*:\\?"(?P<title>.*?)\\?".*?'
    r'jobCity\\?"\s*:\\?"(?P<city>.*?)\\?".*?'
    r'slug\\?"\s*:\\?"(?P<slug>[a-z0-9-]+)\\?"',
    re.S,
)


class HumoParser:
    domain_pattern = r"^https?://(?:www\.)?humo\.tj(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await safe_fetch(client, _BOARD_URL)
        seen: set[str] = set()
        for match in _VACANCY.finditer(response.text):
            title = " ".join(match.group("title").replace('\\"', '"').split())
            city = " ".join(match.group("city").replace('\\"', '"').split())
            slug = match.group("slug")
            if not title or slug in seen:
                continue
            seen.add(slug)
            url = f"{_BOARD_URL}/{slug}"
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "Humo",
                external_id=hashlib.sha256(url.encode()).hexdigest()[:20],
                url=url,
                text=f"{title}\n{city}" if city else title,
                metadata={"board_url": _BOARD_URL, "location": city or None, "parser": "humo_rsc"},
            )
            if len(seen) >= (spec.limit or 50):
                return


register_site_parser("humo", domain_pattern=HumoParser.domain_pattern)(HumoParser)
