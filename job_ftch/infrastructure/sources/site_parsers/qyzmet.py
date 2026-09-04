"""Parser for Qyzmet's server-rendered vacancy cards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    ListingPagination,
    keywords_from_spec,
    normalize_search_keywords,
    paginate_listing,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_LISTING_PATH = "/вакансии"


class QyzmetParser:
    domain_pattern = r"^https?://(?:www\.)?qyzmet\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self, base_url: str, keywords: Any, *, limit: int | None = None
    ) -> list[str]:
        del limit
        if not normalize_search_keywords(keywords):
            return []
        parsed = urlparse(base_url)
        # The homepage is a challenge shell. Cards live on ``/вакансии``.
        path = parsed.path or _LISTING_PATH
        if path.rstrip("/") in {"", "/"}:
            path = _LISTING_PATH
        return [urlunparse(parsed._replace(path=path, query=""))]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(
        html: str,
        page_url: str,
        spec: CareerSiteSpec,
        keywords: list[str] | None = None,
    ) -> list[RawItem]:
        items: list[RawItem] = []
        for card in LexborHTMLParser(html).css("article.job[data-id]"):
            link = card.css_first("a.job-title")
            description = card.css_first(".desc")
            if link is None:
                continue
            title = " ".join(link.text(strip=True).split())
            body = (
                " ".join(description.text(separator=" ", strip=True).split())
                if description is not None
                else ""
            )
            external_id = card.attributes.get("data-id")
            href = str(link.attributes.get("href") or "")
            if not (title and external_id):
                continue
            if "/redir" in href or not href:
                href = f"/jobdesc?id={external_id}"
            text = f"{title}\n{body}".strip()
            if not text_matches_keywords(text, keywords):
                continue
            company_node = card.css_first(".job-data.company")
            location_node = card.css_first(".job-data.region")
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "qyzmet",
                    external_id=external_id,
                    url=urljoin(page_url, href),
                    text=text,
                    metadata={
                        "board_url": spec.url,
                        "company": (
                            " ".join(company_node.text(strip=True).split())
                            if company_node is not None
                            else None
                        ),
                        "location": (
                            " ".join(location_node.text(strip=True).split())
                            if location_node is not None
                            else None
                        ),
                        "parser": "qyzmet",
                        "detail_vacancy_confirmed": True,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        listing_url = spec.url
        if urlparse(listing_url).path.rstrip("/") in {"", "/"}:
            listing_url = urljoin(listing_url, _LISTING_PATH)

        async def fetch(url: str) -> str:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return str(response.text)

        def extract(html: str, url: str) -> list[RawItem]:
            return self._items_from_html(html, url, spec, keywords)

        items = await paginate_listing(
            fetch,
            extract,
            listing_url,
            limit=spec.limit or 50,
            pagination=ListingPagination(max_pages=DEFAULT_LISTING_MAX_PAGES),
            identity=lambda item: item.external_id,
        )
        for item in items:
            yield item


register_site_parser("qyzmet", domain_pattern=QyzmetParser.domain_pattern)(QyzmetParser)
