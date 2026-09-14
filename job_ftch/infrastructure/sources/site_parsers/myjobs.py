"""MyJobs.ge public API parser.

The site renders its vacancy board through a public JSON API.  The browser
monitor sees that traffic, but generic response ranking can select the site's
metadata endpoints instead of the vacancy collection.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


class MyJobsParser:
    """Parse current vacancies and their full public API details."""

    domain_pattern = r"^https?://(?:www\.)?myjobs\.ge(?:/|$)"
    has_custom_parse = True
    terminal_on_error = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        source_name = spec.source_name or "myjobs_ge"
        listing_url = "https://api.myjobs.ge/api/ka/public/vacancies/v2"
        listing = await client.get(listing_url, params={"count": min(limit, 50), "newest": "true"})
        listing.raise_for_status()
        rows = listing.json().get("data", [])

        emitted = 0
        for row in rows:
            if emitted >= limit or not isinstance(row, dict) or not row.get("id"):
                continue
            vacancy_id = str(row["id"])
            detail = await client.get(f"https://api.myjobs.ge/api/ka/public/vacancies/{vacancy_id}")
            detail.raise_for_status()
            payload = detail.json().get("data")
            if not isinstance(payload, dict) or payload.get("status") not in {None, "active"}:
                continue

            country = payload.get("country") or {}
            city = country.get("city") if isinstance(country, dict) else {}
            company = payload.get("company") or {}
            metadata = {
                "board_url": spec.url,
                "job_url": f"https://myjobs.ge/ka/vacancy/{vacancy_id}",
                "company": company.get("brand_name") if isinstance(company, dict) else None,
                "location": city.get("title") if isinstance(city, dict) else None,
                "country": country.get("title") if isinstance(country, dict) else None,
                "salary_from": payload.get("salary_from"),
                "salary_to": payload.get("salary_to"),
                "salary_currency": payload.get("salary_currency"),
                "employment_type": payload.get("employment_type"),
                "job_type": payload.get("job_type"),
                "date_posted": payload.get("created_at"),
                "parser": "myjobs_api",
            }
            text = json.dumps(payload, ensure_ascii=False)
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=vacancy_id,
                url=metadata["job_url"],
                text=text,
                metadata=metadata,
            )
            emitted += 1

    @property
    def __name__(self) -> str:
        return "MyJobsParser"


register_site_parser("myjobs_ge", domain_pattern=MyJobsParser.domain_pattern)(MyJobsParser)
