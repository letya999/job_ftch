import json
from collections.abc import AsyncIterator
from typing import Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteParser


@register_site_parser("breezy", domain_pattern=r"breezy\.hr")
class BreezyParser(SiteParser):
    domain_pattern = r"breezy\.hr"
    has_custom_parse = True
    # Breezy's documented board JSON is authoritative: an empty JSON list is
    # evidence of an empty board, not an invitation to generic crawling.
    confirmed_empty_on_empty = True

    def can_handle(self, spec: CareerSiteSpec, html: str, url: str) -> bool:
        return "breezy.hr" in url

    async def parse(  # type: ignore[override, misc]
        self, spec: CareerSiteSpec, client: Any
    ) -> AsyncIterator[RawItem]:
        """Fetch Breezy's JSON board through the source-owned HTTP client."""
        url = spec.url
        base_url = url.split("?")[0].rstrip("/")
        json_url = f"{base_url}/json" if not base_url.endswith("/json") else base_url

        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        response = await await_with_source_deadline(client.get(json_url))
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return
        if not isinstance(data, list):
            return

        emitted = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            job_url = item.get("url")
            title = item.get("name")
            if not job_url or not title:
                continue
            location_data = item.get("location", {})
            location = (
                location_data.get("name") or location_data.get("city") or "Unknown"
                if isinstance(location_data, dict)
                else "Unknown"
            )
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or url,
                external_id=str(item.get("id") or job_url),
                text=json.dumps(item),
                url=str(job_url),
                metadata={
                    "title": str(title),
                    "location": location,
                    "department": item.get("department"),
                },
            )
            emitted += 1
            if emitted >= (spec.limit or 50):
                return
