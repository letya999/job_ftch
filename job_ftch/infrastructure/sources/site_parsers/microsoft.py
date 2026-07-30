import json
from collections.abc import AsyncIterator
from typing import Any

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item


@register_site_parser("microsoft", domain_pattern=r"careers\.microsoft\.com")
class MicrosoftParser:
    domain_pattern = r"careers\.microsoft\.com"
    has_custom_parse = True
    supports_discover = False

    def can_handle(self, spec: CareerSiteSpec, html: str, url: str) -> bool:
        return "careers.microsoft.com" in url

    def runtime_defaults(self, url: str) -> None:
        del url
        return None

    def parser_kind(self, url: str) -> None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        """Fetch listings through the injected runtime client."""
        from urllib.parse import parse_qsl, urlparse

        api_url = "https://apply.careers.microsoft.com/api/pcsx/search"
        parsed = urlparse(str(spec.url))
        query_params = dict(parse_qsl(parsed.query))
        start = 0
        page_size = min(spec.limit or 50, 50)
        emitted = 0

        while True:
            params: dict[str, str | int] = {
                "domain": "microsoft.com",
                "start": start,
                "limit": page_size,
            }
            params.update(query_params)
            from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

            resp = await await_with_source_deadline(
                client.get(api_url, params=params, headers={"User-Agent": "Mozilla/5.0"})
            )
            resp.raise_for_status()
            data = resp.json()
            positions = data.get("data", {}).get("positions", [])
            if not positions:
                break
            for item in positions:
                job_id = item.get("displayJobId") or item.get("id")
                title = item.get("name")
                locations = item.get("locations", [])
                location: Any = locations[0] if locations else "Unknown"
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "Microsoft",
                    external_id=str(job_id),
                    text=json.dumps(item),
                    url=f"https://jobs.careers.microsoft.com/global/en/job/{job_id}",
                    metadata={"title": title, "location": location},
                )
                emitted += 1
                if emitted >= (spec.limit or 50):
                    return
            if len(positions) < page_size:
                break
            start += page_size
