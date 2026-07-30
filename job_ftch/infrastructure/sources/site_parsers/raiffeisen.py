"""Site-specific parser for career.raiffeisen.ru.

SPA site that loads job data via /api/v2 backend endpoint. Extracts job
listings from the API response for structured data access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.registry import register_site_parser
from job_ftch.config import get_settings
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


class RaiffeisenParser:
    """Parser for career.raiffeisen.ru (SPA with API backend)."""

    domain_pattern = r"^https?://(?:www\.)?career[._]raiffeisen\.ru(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="networkidle",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        source_name = spec.source_name or "raiffeisen"

        # Try API endpoint first
        items = await self._parse_via_api(spec.url, source_name, limit, client)
        if items:
            for item in items:
                yield item
            return

        # Fall back to browser rendering
        from job_ftch.infrastructure.sources.browser_utils import (
            BROWSER_KEYS,
            open_page,
            safe_content,
        )

        browser_config = {k: v for k, v in spec.monitor_config.items() if k in BROWSER_KEYS}
        browser_config.setdefault("headless", True)
        browser_config.setdefault("stealth", True)

        async with open_page(browser_config) as page:
            await await_with_source_deadline(
                page.goto(
                    spec.url,
                    wait_until="networkidle",
                    timeout=int(get_settings().career_site_timeout_seconds * 1000),
                )
            )
            html = await safe_content(page)

        items = self._parse_from_rendered_html(html, spec.url, source_name, limit)
        for item in items:
            yield item

    async def _parse_via_api(
        self, base_url: str, source_name: str, limit: int, client: Any
    ) -> list[RawItem]:
        """Try to parse jobs via Raiffeisen API."""
        api_endpoints = [
            f"{base_url.rstrip('/')}/api/v2/vacancies",
            f"{base_url.rstrip('/')}/api/v2/jobs",
            f"{base_url.rstrip('/')}/api/vacancies",
        ]
        for api_url in api_endpoints:
            try:
                response = await await_with_source_deadline(
                    client.get(
                        api_url,
                        follow_redirects=True,
                        timeout=get_settings().career_site_timeout_seconds,
                        headers={"Accept": "application/json"},
                    )
                )
                if response.status_code != 200:
                    continue
                data = response.json()
                return self._parse_api_response(data, base_url, source_name, limit)
            except Exception as exc:
                logger.warning("raiffeisen.api_fetch_failed", url=api_url, error=str(exc))
                continue
        return []

    def _parse_api_response(
        self, data: Any, board_url: str, source_name: str, limit: int
    ) -> list[RawItem]:
        """Parse jobs from API response."""
        items: list[RawItem] = []
        # Handle different API response structures
        jobs = []
        if isinstance(data, list):
            jobs = data
        elif isinstance(data, dict):
            # Try common keys
            for key in ("items", "vacancies", "jobs", "results", "data"):
                if key in data and isinstance(data[key], list):
                    jobs = data[key]
                    break
        for job in jobs[:limit]:
            if not isinstance(job, dict):
                continue
            title = job.get("title") or job.get("name") or job.get("position")
            if not title:
                continue
            job_url = job.get("url") or job.get("link") or job.get("applyUrl") or board_url
            if isinstance(job_url, dict):
                job_url = job_url.get("href", board_url)
            external_id = job.get("id") or job.get("vacancyId") or job_url
            location = job.get("location") or job.get("city") or ""
            if isinstance(location, dict):
                location = location.get("name", "")
            description = job.get("description") or job.get("requirements") or ""
            text_parts = [str(title)]
            if location:
                text_parts.append(str(location))
            if description:
                desc_clean = " ".join(str(description).split())
                if desc_clean:
                    text_parts.append(desc_clean)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=str(external_id),
                    url=str(job_url),
                    text="\n".join(part for part in text_parts if part),
                    metadata={
                        "board_url": board_url,
                        "job_url": str(job_url),
                        "location": str(location) if location else None,
                        "parser": "raiffeisen_api",
                    },
                )
            )
        return items

    def _parse_from_rendered_html(
        self, html: str, url: str, source_name: str, limit: int
    ) -> list[RawItem]:
        """Parse jobs from rendered HTML (fallback)."""
        from selectolax.lexbor import LexborHTMLParser

        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # Generic selectors for job cards
        job_cards = parser.css(
            '.job-card, .vacancy-card, [class*="vacancy"], [class*="job-item"], article'
        )

        for card in job_cards[:limit]:
            link_node = card.css_first("a[href]")
            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = href if href.startswith("http") else f"{url.rstrip('/')}/{href.lstrip('/')}"

            title_node = card.css_first('h2, h3, h4, .title, [class*="title"]')
            title = _clean_text(title_node.text(separator=" ", strip=True)) if title_node else None
            if not title:
                title = _clean_text(link_node.text(separator=" ", strip=True))
            if not title or len(title) < 3:
                continue

            location_node = card.css_first('.location, [class*="location"]')
            location = (
                _clean_text(location_node.text(separator=" ", strip=True))
                if location_node
                else None
            )

            text_parts = [title]
            if location:
                text_parts.append(location)

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
                        "parser": "raiffeisen_html",
                    },
                )
            )

        return items

    @property
    def __name__(self) -> str:
        return "RaiffeisenParser"


register_site_parser(
    "raiffeisen",
    domain_pattern=RaiffeisenParser.domain_pattern,
)(RaiffeisenParser)
