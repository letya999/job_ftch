"""Official HH employer-board fallbacks for unreachable company career sites."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.hh import _parse_iso_datetime, _strip_html

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
        match = re.search(r"/employer/(\d+)", self.employer_url)
        if match is None:
            raise ValueError("HH employer URL has no numeric employer id")
        async for item in parse_hh_employer_api(spec, client, employer_id=match.group(1)):
            yield item


async def parse_hh_employer_api(
    spec: CareerSiteSpec,
    client: Any,
    *,
    employer_id: str,
) -> AsyncIterator[RawItem]:
    limit = min(spec.limit or 50, 100)
    response = await client.get(
        "https://api.hh.ru/vacancies",
        params={"employer_id": employer_id, "per_page": limit, "order_by": "publication_time"},
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("HH employer API returned an unknown payload")
    source_name = spec.source_name or f"hh_employer_{employer_id}"
    for vacancy in items[:limit]:
        if not isinstance(vacancy, dict):
            continue
        external_id = str(vacancy.get("id") or "").strip()
        title = str(vacancy.get("name") or "").strip()
        url = str(vacancy.get("alternate_url") or "").strip()
        if not external_id or not title or not url:
            continue
        snippet = vacancy.get("snippet") if isinstance(vacancy.get("snippet"), dict) else {}
        employer = vacancy.get("employer") if isinstance(vacancy.get("employer"), dict) else {}
        area = vacancy.get("area") if isinstance(vacancy.get("area"), dict) else {}
        text = "\n".join(
            value
            for value in (
                title,
                str(employer.get("name") or "").strip(),
                str(area.get("name") or "").strip(),
                _strip_html(str(snippet.get("requirement") or "")),
                _strip_html(str(snippet.get("responsibility") or "")),
            )
            if value
        )
        yield build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=external_id,
            url=url,
            text=text,
            created_at=_parse_iso_datetime(
                str(vacancy.get("published_at") or vacancy.get("created_at") or "")
            ),
            metadata={
                "board_url": spec.url,
                "job_url": url,
                "company": employer.get("name"),
                "locations": [area.get("name")] if area.get("name") else None,
                "parser": "hh_employer_api",
                "detail_vacancy_confirmed": True,
            },
        )


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
