"""Site-specific parser for btsdigital.kz.

SSR site with jobs visible in HTML. Extracts job listings from structured
HTML elements with gtag tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


class BtsdigitalParser:
    """Parser for btsdigital.kz career page."""

    domain_pattern = r"^https?://(?:www\.)?btsdigital\.kz(?:/|$)"
    has_custom_parse = True
    # The public Tilda career page is the source of truth.  When it contains
    # no job cards, generic crawling cannot discover an additional board.
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
        limit = spec.limit or 50
        source_name = spec.source_name or "btsdigital"

        # Keep custom parsing on the shared source client.
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        # ``/en/career`` was published in the fixture but is no longer served
        # by BTS.  Their current public career board is the Russian canonical
        # page; use it rather than treating a stale locale alias as a crawler
        # failure.
        board_url = spec.url
        if board_url.rstrip("/").endswith("/en/career"):
            board_url = f"{board_url.rstrip('/')[: -len('/en/career')]}/ru/career"

        response = await await_with_source_deadline(client.get(board_url, follow_redirects=True))
        response.raise_for_status()
        html = response.text

        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # Look for job listing elements
        job_cards = parser.css(
            '.job-card, .vacancy-card, [class*="vacancy"], [class*="job"], .career-item, article'
        )

        if not job_cards:
            # Fallback: find all links that look like job postings
            job_cards = parser.css('a[href*="/career/"], a[href*="/vacancy/"], a[href*="/job/"]')

        for card in job_cards[:limit]:
            link_node = card if card.tag == "a" else card.css_first("a[href]")

            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = urljoin(board_url, href)

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
                        "board_url": board_url,
                        "job_url": job_url,
                        "location": location,
                        "parser": "btsdigital",
                    },
                )
            )

        for item in items:
            yield item

    @property
    def __name__(self) -> str:
        return "BtsdigitalParser"


register_site_parser(
    "btsdigital",
    domain_pattern=BtsdigitalParser.domain_pattern,
)(BtsdigitalParser)
