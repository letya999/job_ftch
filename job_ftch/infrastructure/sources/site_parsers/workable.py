"""Site-specific parser for apply.workable.com.

Workable boards render job listings via Cloudfront JS bundle. The DOM contains
job cards with structured selectors that can be extracted after page render.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.config import get_settings
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


class WorkableParser:
    """Parser for apply.workable.com job boards."""

    domain_pattern = r"^https?://apply\.workable\.com(?:/|$)"
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
        del client
        limit = spec.limit or 50
        source_name = spec.source_name or "workable"

        # Workable pages need JS render - the jobs are in the DOM
        # We'll extract from the rendered HTML
        from job_ftch.infrastructure.sources.browser_utils import (
            BROWSER_KEYS,
            open_page,
            safe_content,
        )

        browser_config = {k: v for k, v in spec.monitor_config.items() if k in BROWSER_KEYS}
        browser_config.setdefault("headless", True)
        browser_config.setdefault("stealth", True)

        async with open_page(browser_config) as page:
            await page.goto(
                spec.url,
                wait_until="networkidle",
                timeout=int(get_settings().career_site_timeout_seconds * 1000),
            )
            html = await safe_content(page)

        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # Workable job card selectors
        job_cards = parser.css(
            '.job-card, .job-card__details, [class*="jobCard"], '
            '[data-ui="job-card"], .styles__JobCard'
        )

        if not job_cards:
            # Fallback: try to find any links that look like job postings
            job_cards = parser.css('a[href*="/apply/"], a[href*="/jobs/"]')

        for card in job_cards[:limit]:
            link_node = card.css_first("a[href]") if card.tag != "a" else card
            if not link_node:
                link_node = card.css_first("a[href]")

            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = (
                href if href.startswith("http") else f"{spec.url.rstrip('/')}/{href.lstrip('/')}"
            )

            title_node = card.css_first('.job-card__title, .job-title, [class*="title"], h2, h3')
            title = _clean_text(title_node.text(separator=" ", strip=True)) if title_node else None
            if not title:
                title = _clean_text(link_node.text(separator=" ", strip=True))
            if not title or len(title) < 3:
                continue

            location_node = card.css_first('.job-card__location, .location, [class*="location"]')
            location = (
                _clean_text(location_node.text(separator=" ", strip=True))
                if location_node
                else None
            )

            department_node = card.css_first(
                '.job-card__department, .department, [class*="department"]'
            )
            department = (
                _clean_text(department_node.text(separator=" ", strip=True))
                if department_node
                else None
            )

            text_parts = [title]
            if department:
                text_parts.append(department)
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
                        "board_url": spec.url,
                        "job_url": job_url,
                        "location": location,
                        "department": department,
                        "parser": "workable",
                    },
                )
            )

        for item in items:
            yield item

    @property
    def __name__(self) -> str:
        return "WorkableParser"


register_site_parser(
    "workable",
    domain_pattern=WorkableParser.domain_pattern,
)(WorkableParser)
