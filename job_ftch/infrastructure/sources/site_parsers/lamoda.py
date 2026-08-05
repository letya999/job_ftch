"""Parser for Lamoda's public career API."""

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


_BOARD_URL = "https://job.lamoda.ru/vacancies"
_API_URL = "https://job.lamoda.ru/api/hr/vacancies/compact"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _nested_name(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return _clean(value.get("name")) if isinstance(value, dict) else ""


class LamodaParser:
    """Use Lamoda's public HR API instead of the stale lamoda.ru careers page."""

    domain_pattern = r"^https?://(?:(?:www\.)?lamoda\.ru/careers|job\.lamoda\.ru)(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        response = await client.get(
            _API_URL,
            params={
                "search": "",
                "minExperience": "",
                "pagination[start]": 0,
                "pagination[limit]": limit,
            },
            headers={
                "Accept": "application/json",
                "Referer": _BOARD_URL,
                "User-Agent": "Mozilla/5.0",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return

        source_name = spec.source_name or "lamoda"
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            vacancy_id = row.get("id")
            title = _clean(row.get("name"))
            slug = _clean(row.get("slug"))
            if not vacancy_id or not title or not slug:
                continue
            job_url = f"{_BOARD_URL}/{slug}"
            location = _nested_name(row, "location")
            department = _nested_name(row, "department")
            direction = _nested_name(row, "direction")
            short_info = _clean(row.get("shortInfo"))
            published_at = _clean(row.get("externalPublicationDate"))
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=str(vacancy_id),
                url=job_url,
                text="\n".join(
                    part
                    for part in (
                        title,
                        location,
                        department,
                        direction,
                        short_info,
                    )
                    if part
                ),
                metadata={
                    "board_url": _BOARD_URL,
                    "job_url": job_url,
                    "location": location or None,
                    "department": department or None,
                    "direction": direction or None,
                    "published_at": published_at or None,
                    "parser": "lamoda_public_hr_api",
                },
            )


register_site_parser("lamoda", domain_pattern=LamodaParser.domain_pattern)(LamodaParser)
