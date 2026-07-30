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


class RelocateMeParser:
    """Custom parser for relocate.me job board."""

    domain_pattern = r"^https?://(?:www\.)?relocate\.me(?:/|$)"
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
        return "relocate.me"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        source_name = spec.source_name or "relocateme.eu"

        response = await client.get(spec.url, follow_redirects=True)
        html = response.text

        parser = LexborHTMLParser(html)
        job_cards = parser.css(".jobs-list__job, .job")

        count = 0
        for card in job_cards:
            if count >= limit:
                break

            link_node = card.css_first("a[href]")
            if not link_node:
                continue

            href = link_node.attributes.get("href")
            if not href:
                continue

            job_url = urljoin(spec.url, href)

            title_node = card.css_first(".job__title, .job-title, h3, h2, b")
            title = (
                _clean_text(title_node.text(separator=" ", strip=True))
                if title_node
                else _clean_text(link_node.text(separator=" ", strip=True))
            )

            if not title or len(title) < 3:
                continue

            company_node = card.css_first(".job__company, .company")
            company = (
                _clean_text(company_node.text(separator=" ", strip=True)) if company_node else None
            )

            location_node = card.css_first(".job__location, .location")
            location = (
                _clean_text(location_node.text(separator=" ", strip=True))
                if location_node
                else None
            )

            text_parts = [title]
            if company:
                text_parts.append(company)
            if location:
                text_parts.append(location)

            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=job_url,
                url=job_url,
                text="\\n".join(part for part in text_parts if part),
                metadata={
                    "board_url": spec.url,
                    "job_url": job_url,
                    "location": location,
                    "company": company,
                    "parser": "relocateme_html",
                },
            )
            count += 1


register_site_parser("relocateme", domain_pattern=RelocateMeParser.domain_pattern)(RelocateMeParser)
