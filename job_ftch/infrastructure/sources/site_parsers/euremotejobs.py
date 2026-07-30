"""Site-specific parser for euremotejobs.com.

WordPress/Jobify site with job listings in SSR HTML. Uses WordPress Job Manager
plugin with job cards in `.job-card` elements. Can also access REST API at
`/wp-json/` for structured data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import structlog
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


class EuremotejobsParser:
    """Parser for euremotejobs.com (WordPress/Jobify)."""

    domain_pattern = r"^https?://(?:www\.)?euremotejobs\.com(?:/|$)"
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
        source_name = spec.source_name or "euremotejobs"

        # Try REST API first for structured data
        items = await self._parse_via_api(spec.url, source_name, limit, client)
        if items:
            for item in items:
                yield item
            return

        # Fall back to HTML parsing
        response = await client.get(spec.url, follow_redirects=True)
        html = response.text
        items = self._parse_from_html(html, spec.url, source_name, limit)
        for item in items:
            yield item

    async def _parse_via_api(
        self, url: str, source_name: str, limit: int, client: Any
    ) -> list[RawItem]:
        """Try to parse jobs via WordPress REST API."""
        api_urls = [
            f"{url.rstrip('/')}/wp-json/wp/v2/job_listing?per_page={limit}",
            f"{url.rstrip('/')}/wp-json/jobs/v1/listings?per_page={limit}",
        ]
        for api_url in api_urls:
            try:
                # The injected client carries the source-wide timeout budget.
                # Do not shorten it here: a parser-local timeout otherwise
                # turns a merely slow ATS response into a false empty board.
                response = await client.get(api_url, follow_redirects=True)
                if response.status_code != 200:
                    continue
                data = response.json()
                if not isinstance(data, list):
                    continue
                return self._parse_api_response(data, url, source_name)
            except Exception as exc:
                logger.warning("euremotejobs.api_fetch_failed", url=api_url, error=str(exc))
                continue
        return []

    def _parse_api_response(
        self, data: list[dict[str, Any]], board_url: str, source_name: str
    ) -> list[RawItem]:
        """Parse jobs from WordPress REST API response."""
        items: list[RawItem] = []
        for job in data:
            title = job.get("title", {})
            if isinstance(title, dict):
                title = title.get("rendered", "")
            if not title:
                continue
            link = job.get("link", board_url)
            job_id = job.get("id", link)
            location = job.get("job_location", "")
            if isinstance(location, dict):
                location = location.get("name", "")
            employment_type = job.get("employment_type", "")
            description = job.get("content", {})
            if isinstance(description, dict):
                description = description.get("rendered", "")
            text_parts = [title]
            if location:
                text_parts.append(str(location))
            if employment_type:
                text_parts.append(str(employment_type))
            if description:
                # Strip HTML tags for clean text
                import re

                desc_clean = re.sub(r"<[^>]+>", " ", str(description))
                desc_clean = " ".join(desc_clean.split())
                if desc_clean:
                    text_parts.append(desc_clean)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=str(job_id),
                    url=link,
                    text="\n".join(part for part in text_parts if part),
                    metadata={
                        "board_url": board_url,
                        "job_url": link,
                        "location": str(location) if location else None,
                        "employment_type": str(employment_type) if employment_type else None,
                        "parser": "euremotejobs_api",
                    },
                )
            )
        return items

    def _parse_from_html(self, html: str, url: str, source_name: str, limit: int) -> list[RawItem]:
        """Parse jobs from HTML using WordPress Job Manager selectors."""
        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # WordPress Job Manager job cards
        job_cards = parser.css(
            '.job-card, .job_listing, [class*="job-listing"], '
            '[class*="job_listing"], .wp-job-manager'
        )

        for card in job_cards[:limit]:
            link_node = card.css_first("a[href]")
            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = urljoin(url, href)

            title_node = card.css_first(
                ".job-title, .job-card__title, .job_listing-title, h3, h4, .title"
            )
            title = _clean_text(title_node.text(separator=" ", strip=True)) if title_node else None
            if not title:
                title = _clean_text(link_node.text(separator=" ", strip=True))
            if not title or len(title) < 3:
                continue

            location_node = card.css_first(
                '.meta-location, .job-card__location, .location, [class*="location"]'
            )
            location = (
                _clean_text(location_node.text(separator=" ", strip=True))
                if location_node
                else None
            )

            type_node = card.css_first('.meta-type, .job-card__type, .type, [class*="type"]')
            job_type = _clean_text(type_node.text(separator=" ", strip=True)) if type_node else None

            text_parts = [title]
            if location:
                text_parts.append(location)
            if job_type:
                text_parts.append(job_type)

            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=job_url,
                    url=job_url,
                    text="\n".join(part for part in text_parts if part),
                    metadata={
                        "board_url": url,
                        "job_url": job_url,
                        "location": location,
                        "job_type": job_type,
                        "parser": "euremotejobs_html",
                    },
                )
            )

        return items

    @property
    def __name__(self) -> str:
        return "EuremotejobsParser"


register_site_parser(
    "euremotejobs",
    domain_pattern=EuremotejobsParser.domain_pattern,
)(EuremotejobsParser)
