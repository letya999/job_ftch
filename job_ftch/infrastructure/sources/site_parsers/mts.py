"""Browser-backed parser for the client-rendered MTS vacancy board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class MtsParser:
    domain_pattern = r"^https?://job\.mts\.ru(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await client.get("https://job.mts.ru/api/v2/vacancies", follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        vacancies = payload.get("data", []) if isinstance(payload, dict) else []
        emitted = 0
        for vacancy in vacancies:
            if not isinstance(vacancy, dict) or not vacancy.get("isActive", False):
                continue
            title = str(vacancy.get("title") or "").strip()
            slug = str(vacancy.get("slug") or vacancy.get("documentId") or "").strip()
            if not title or not slug:
                continue
            url = f"https://job.mts.ru/vacancies/{slug}"
            text = "\n".join(
                str(value).strip()
                for value in (title, vacancy.get("description"), vacancy.get("tasks"))
                if value
            )
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "mts",
                external_id=slug,
                url=url,
                text=text,
                metadata={
                    "board_url": spec.url,
                    "date_posted": vacancy.get("publishedAt") or vacancy.get("createdAt"),
                    "parser": "mts_api",
                },
            )
            emitted += 1
            if spec.limit and emitted >= spec.limit:
                return


register_site_parser("mts", domain_pattern=MtsParser.domain_pattern)(MtsParser)
