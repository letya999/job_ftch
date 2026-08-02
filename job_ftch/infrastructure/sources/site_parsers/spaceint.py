"""Site parser for Space International's Breezy-backed careers page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BREEZY_PORTAL = "https://space-ge.breezy.hr"
_BREEZY_JSON = f"{_BREEZY_PORTAL}/json"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


class SpaceIntParser:
    """Use Space Int's linked Breezy board instead of crawling Wix markup."""

    domain_pattern = r"^https?://(?:www\.)?spaceint\.ge(?:/|$)"
    has_custom_parse = True
    terminal_on_empty = True
    confirmed_empty_on_empty = True

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
        response = await client.get(_BREEZY_JSON, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return

        source_name = spec.source_name or "spaceint"
        for row in data[: spec.limit or 50]:
            if not isinstance(row, dict):
                continue
            title = _clean(row.get("name") or row.get("title"))
            if not title:
                continue
            raw_url = _clean(row.get("url") or row.get("shortlink") or row.get("friendly_id"))
            job_url = urljoin(f"{_BREEZY_PORTAL}/", raw_url) if raw_url else _BREEZY_PORTAL
            location = _clean(row.get("location") or row.get("city"))
            department = _clean(row.get("department"))
            description = _clean(row.get("description"))
            text = "\n".join(part for part in (title, location, department, description) if part)
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=_clean(row.get("_id") or row.get("id") or job_url),
                url=job_url,
                text=text,
                metadata={
                    "board_url": _BREEZY_PORTAL,
                    "job_url": job_url,
                    "location": location or None,
                    "department": department or None,
                    "parser": "spaceint_breezy_json",
                },
            )


register_site_parser("spaceint", domain_pattern=SpaceIntParser.domain_pattern)(SpaceIntParser)
