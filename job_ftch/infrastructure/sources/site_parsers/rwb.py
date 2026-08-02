"""Parser for public RWB/Wildberries career API."""

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


_BOARD_URL = "https://career.rwb.ru/vacancies"
_API_URL = "https://career.rwb.ru/crm-api/api/v1/pub/vacancies"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


class RwbCareerParser:
    """Use RWB public vacancy API instead of blocked legacy Wildberries URLs."""

    domain_pattern = (
        r"^https?://(?:(?:www\.)?wildberries\.ru/services/trudoustroystvo|"
        r"career\.rwb\.ru|job\.wb\.ru)(?:/|$)"
    )
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
            params={"limit": limit, "offset": 0},
            headers={"Accept": "application/json", "Referer": _BOARD_URL},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return

        source_name = spec.source_name or "rwb"
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            vacancy_id = row.get("id")
            title = _clean(row.get("name"))
            if not vacancy_id or not title:
                continue
            job_url = f"{_BOARD_URL}/{vacancy_id}"
            direction = _clean(row.get("direction_title"))
            role = _clean(row.get("direction_role_title"))
            experience = _clean(row.get("experience_type_title"))
            city = _clean(row.get("city_title"))
            employment = ", ".join(
                _clean(item.get("title"))
                for item in row.get("employment_types", [])
                if isinstance(item, dict) and _clean(item.get("title"))
            )
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=str(vacancy_id),
                url=job_url,
                text="\n".join(
                    part for part in (title, direction, role, experience, city, employment) if part
                ),
                metadata={
                    "board_url": _BOARD_URL,
                    "job_url": job_url,
                    "direction": direction or None,
                    "role": role or None,
                    "experience": experience or None,
                    "location": city or None,
                    "employment": employment or None,
                    "parser": "rwb_public_api",
                },
            )


register_site_parser("rwb", domain_pattern=RwbCareerParser.domain_pattern)(RwbCareerParser)
