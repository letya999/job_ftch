"""Parser for TomTom's public careers API."""

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


_BOARD_URL = "https://www.tomtom.com/careers/joboverview/"
_API_URL = "https://www.tomtom.com/api/careers/jobs/"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _description_text(description: Any) -> str:
    if not isinstance(description, dict):
        return ""
    parts: list[str] = []
    for item in description.get("body") or []:
        text = _clean(item)
        if text:
            parts.append(text)
    for section in description.get("sections") or []:
        if not isinstance(section, dict):
            continue
        title = _clean(section.get("title"))
        if title:
            parts.append(title)
        for item in section.get("items") or []:
            if isinstance(item, dict):
                text = _clean(item.get("text"))
                if text:
                    parts.append(text)
    return "\n".join(parts)


class TomTomParser:
    """Read TomTom jobs from the same public JSON API used by the careers page."""

    domain_pattern = r"^https?://(?:www\.)?tomtom\.com/careers(?:/|$)"
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
            headers={
                "Accept": "application/json",
                "Referer": _BOARD_URL,
                "User-Agent": "Mozilla/5.0",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return

        source_name = spec.source_name or "tomtom"
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            job_id = _clean(row.get("jobId"))
            slug = _clean(row.get("slug"))
            title = _clean(row.get("title"))
            if not job_id or not slug or not title:
                continue
            job_url = f"https://www.tomtom.com/careers/jobdetails/{job_id}/{slug}/"
            team = _clean(row.get("team"))
            department = _clean(row.get("department"))
            location = _clean(row.get("location"))
            category = _clean(row.get("category"))
            description = _description_text(row.get("description"))
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=job_id,
                url=job_url,
                text="\n".join(
                    part
                    for part in (title, category, team, department, location, description)
                    if part
                ),
                metadata={
                    "board_url": _BOARD_URL,
                    "job_url": job_url,
                    "category": category or None,
                    "team": team or None,
                    "department": department or None,
                    "location": location or None,
                    "parser": "tomtom_public_careers_api",
                },
            )


register_site_parser("tomtom", domain_pattern=TomTomParser.domain_pattern)(TomTomParser)
