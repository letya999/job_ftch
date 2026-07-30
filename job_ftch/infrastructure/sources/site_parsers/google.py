import json
import re
from collections.abc import AsyncIterator
from typing import Any, cast
from urllib.parse import urlparse

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteParser


@register_site_parser(
    "google", domain_pattern=r"careers\.google\.com|www\.google\.com/about/careers"
)
class GoogleParser(SiteParser):
    domain_pattern = r"careers\.google\.com|www\.google\.com/about/careers"
    has_custom_parse = True

    def can_handle(self, spec: CareerSiteSpec, html: str, url: str) -> bool:
        return "careers.google.com" in url or "google.com/about/careers" in url

    async def parse(  # type: ignore[override, misc]
        self, spec: CareerSiteSpec, client: Any
    ) -> AsyncIterator[RawItem]:
        """Extract the embedded Google job payload with the injected client."""
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        parsed_url = urlparse(spec.url)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        location_slug: str | None = None
        if "locations" in path_parts:
            location_slug = path_parts[-1].casefold()
            fetch_url = (
                "https://www.google.com/about/careers/applications/jobs/results/"
                f"?location={location_slug}"
            )
        else:
            fetch_url = spec.url
        response = await await_with_source_deadline(
            client.get(fetch_url, headers={"User-Agent": "Mozilla/5.0"})
        )
        response.raise_for_status()
        matches = re.findall(
            r"AF_initDataCallback\(\{key: 'ds:1', hash: '[^']+', data:(.*?), sideChannel:",
            response.text,
        )
        if not matches:
            matches = re.findall(
                r"AF_initDataCallback\(\{key: '[^']+', hash: '[^']+', data:(.*?)\}\);</script>",
                response.text,
            )

        emitted = 0
        for match in matches:
            data = self._decode_callback_data(match)
            if data is None:
                continue
            for job in self._extract_job_arrays(data):
                if len(job) < 3:
                    continue
                serialized_job = json.dumps(job)
                if location_slug is not None and location_slug not in serialized_job.casefold():
                    continue
                job_id = job[0]
                title = str(job[1])
                slug = re.sub(r"[^a-z0-9\-]", "", title.replace(" ", "-").lower())
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "Google",
                    external_id=str(job_id),
                    text=serialized_job,
                    url=(
                        "https://www.google.com/about/careers/applications/jobs/results/"
                        f"{job_id}-{slug}"
                    ),
                    metadata={
                        "title": title,
                        "location": location_slug.replace("-", " ").title()
                        if location_slug
                        else "Unknown",
                    },
                )
                emitted += 1
                if emitted >= (spec.limit or 50):
                    return

    @staticmethod
    def _decode_callback_data(match: str) -> object | None:
        value = match.strip()
        depth = 0
        for index, char in enumerate(value):
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    value = value[: index + 1]
                    break
        try:
            return cast("object", json.loads(value))
        except json.JSONDecodeError:
            return None

    def _extract_job_arrays(self, data: object) -> list[list[object]]:
        jobs: list[list[object]] = []
        if isinstance(data, list):
            if (
                len(data) > 5
                and isinstance(data[0], str)
                and data[0].isdigit()
                and len(data[0]) > 10
                and isinstance(data[1], str)
            ):
                jobs.append(data)
            else:
                for item in data:
                    jobs.extend(self._extract_job_arrays(item))
        return jobs
