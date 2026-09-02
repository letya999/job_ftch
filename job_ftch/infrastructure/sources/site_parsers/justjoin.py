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

_DOMAIN_PATTERN = r"justjoin\.it"


@register_site_parser("site_justjoin", domain_pattern=_DOMAIN_PATTERN)
class JustjoinItParser:
    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = True
    terminal_on_error = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter={"include": [r"/job-offer/"], "exclude": [r"/job-offers/"]}
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "site_justjoin"

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        limit = spec.limit or getattr(manifest_entry, "limit", None) or 50
        source_name = spec.source_name or "justjoin_it"
        headers = getattr(manifest_entry, "headers", None) or {
            "Version": "2",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://justjoin.it",
            "Referer": "https://justjoin.it/",
        }
        api_url = (
            getattr(manifest_entry, "api_url", None)
            or "https://api.justjoin.it/v2/user-panel/offers"
        )

        response = await client.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()

        seen: set[str] = set()
        count = 0
        for offer in data.get("data", []):
            if count >= limit:
                break

            slug = str(offer.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)

            locations = [
                str(loc.get("city"))
                for loc in offer.get("multilocation", [])
                if isinstance(loc, dict) and loc.get("city")
            ]
            employment_types = offer.get("employmentTypes") or []
            primary_employment = (
                employment_types[0].get("type")
                if employment_types and isinstance(employment_types[0], dict)
                else None
            )

            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=slug,
                url=f"https://justjoin.it/job-offer/{slug}",
                text=json.dumps(offer, ensure_ascii=False),
                metadata={
                    "title": offer.get("title"),
                    "date_posted": offer.get("publishedAt"),
                    "locations": locations,
                    "employment_type": primary_employment,
                    "parser": "site_justjoin",
                },
            )
            count += 1
