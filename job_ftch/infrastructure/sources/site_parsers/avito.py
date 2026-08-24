"""Site-specific parser for avito.ru career page.

SSR site with JSON-LD structured data and job cards in HTML. Extracts job
listings from both JSON-LD and DOM elements.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
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


class _JsonLdExtractor(HTMLParser):
    """Extract JSON-LD blocks from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.results: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attr_dict = dict(attrs)
            if attr_dict.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._data = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._data).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    self.results.append(parsed)
                except json.JSONDecodeError:
                    pass


class AvitoCareerParser:
    """Parser for avito.ru career page."""

    domain_pattern = r"^https?://(?:(?:[a-z0-9-]+\.)?(?:avito\.ru|career\.avito\.com))(?:/|$)"
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
        source_name = spec.source_name or "avito"

        # Fetch through the source client so retry, SSRF guard, proxy and
        # connection-pool policies remain identical to generic ingestion.
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        response = await await_with_source_deadline(client.get(spec.url, follow_redirects=True))
        html = response.text

        # Try JSON-LD first
        items = self._parse_from_jsonld(html, spec.url, source_name, limit)
        if items:
            for item in items:
                yield item
            return

        # Fall back to HTML parsing
        items = self._parse_from_html(html, spec.url, source_name, limit)
        for item in items:
            yield item

    def _parse_from_jsonld(
        self, html: str, url: str, source_name: str, limit: int
    ) -> list[RawItem]:
        """Extract jobs from JSON-LD structured data."""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        items: list[RawItem] = []

        for block in extractor.results:
            if not isinstance(block, dict):
                continue
            type_val = block.get("@type", "")
            if "JobPosting" not in str(type_val):
                continue
            title = block.get("title") or block.get("name")
            if not title:
                continue
            job_url = block.get("url") or url
            if isinstance(job_url, dict):
                job_url = job_url.get("id", url)
            external_id = block.get("identifier") or job_url
            if isinstance(external_id, dict):
                external_id = external_id.get("id", job_url)
            description = block.get("description", "")
            location_raw = block.get("jobLocation")
            location = ""
            if isinstance(location_raw, dict):
                location = location_raw.get("name", "")
            elif isinstance(location_raw, list) and location_raw:
                first = location_raw[0]
                if isinstance(first, dict):
                    location = first.get("name", "")
            text_parts = [str(title)]
            if location:
                text_parts.append(str(location))
            if description:
                desc_clean = re.sub(r"<[^>]+>", " ", str(description))
                desc_clean = " ".join(desc_clean.split())
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
                        "board_url": url,
                        "job_url": str(job_url),
                        "location": str(location) if location else None,
                        "parser": "avito_jsonld",
                    },
                )
            )
            if len(items) >= limit:
                break
        return items

    def _parse_from_html(self, html: str, url: str, source_name: str, limit: int) -> list[RawItem]:
        """Extract jobs from HTML elements."""
        parser = LexborHTMLParser(html)
        items: list[RawItem] = []

        # Avito career job cards
        job_cards = parser.css(
            '.main-team--job, .job-card, [class*="job"], [class*="vacancy"], .career-item'
        )

        for card in job_cards[:limit]:
            link_node = card.css_first("a[href]")
            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = urljoin(url, href)

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
                        "parser": "avito_html",
                    },
                )
            )

        return items

    @property
    def __name__(self) -> str:
        return "AvitoCareerParser"


register_site_parser(
    "avito",
    domain_pattern=AvitoCareerParser.domain_pattern,
)(AvitoCareerParser)
