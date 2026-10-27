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
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    text_matches_keywords,
    with_query_params,
)

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


_DETAIL_RE = re.compile(r"/vacancies/[a-z0-9-]+/(\d+)/?$", re.IGNORECASE)


class AvitoCareerParser:
    """Parser for avito.ru career page."""

    domain_pattern = r"^https?://(?:(?:[a-z0-9-]+\.)?(?:avito\.ru|career\.avito\.com))(?:/|$)"
    has_custom_parse = True
    supports_search = True
    search_mode = "combined"

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        listing = base_url.split("?", 1)[0] or "https://career.avito.com/vacancies/"
        return [with_query_params(listing, {"q": " OR ".join(terms)})]

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
        keywords = keywords_from_spec(spec)
        from job_ftch.infrastructure.sources.source_deadline import await_with_source_deadline

        async def fetch(url: str) -> str:
            response = await await_with_source_deadline(client.get(url, follow_redirects=True))
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            page_items = self._parse_from_jsonld(html, url, source_name, max(limit, 50))
            if not page_items:
                page_items = self._parse_from_html(html, url, source_name, max(limit, 50))
            return [
                item
                for item in page_items
                if text_matches_keywords(f"{item.url}\n{item.text}", keywords)
            ]

        items = await paginate_listing(
            fetch,
            extract,
            spec.url,
            limit=limit,
            identity=lambda item: item.url,
        )
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
        """Extract jobs from HTML vacancy anchors ``/vacancies/{direction}/{id}/``."""
        parser = LexborHTMLParser(html)
        items: list[RawItem] = []
        seen: set[str] = set()

        for anchor in parser.css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            job_url = urljoin(url, href.split("?", 1)[0])
            match = _DETAIL_RE.search(job_url)
            if match is None or job_url in seen:
                continue
            title = _clean_text(anchor.text(separator=" ", strip=True))
            if not title or len(title) < 3:
                parent = anchor.parent
                title = _clean_text(parent.text(separator=" ", strip=True)) if parent else ""
            if not title or len(title) < 3:
                continue
            seen.add(job_url)
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=job_url,
                    text=title,
                    metadata={
                        "board_url": url,
                        "job_url": job_url,
                        "parser": "avito_html",
                    },
                )
            )
            if len(items) >= limit:
                break

        return items

    @property
    def __name__(self) -> str:
        return "AvitoCareerParser"


register_site_parser(
    "avito",
    domain_pattern=AvitoCareerParser.domain_pattern,
)(AvitoCareerParser)
