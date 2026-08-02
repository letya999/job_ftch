"""Site parser for Sololearn's linked BambooHR careers board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.monitors.shared import BoardGoneError
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BAMBOO_JOBS_URL = "https://sololearn.bamboohr.com/jobs/"


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


class SololearnParser:
    """Follow Sololearn's public ATS link and detect when it has gone stale."""

    domain_pattern = r"^https?://(?:www\.)?sololearn\.com/(?:[a-z]{2}/)?career(?:/|$)"
    has_custom_parse = True
    terminal_on_empty = True

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
        response = await client.get(_BAMBOO_JOBS_URL, follow_redirects=True)
        response.raise_for_status()
        final_url = str(response.url)
        parsed_final = urlparse(final_url)
        if parsed_final.hostname == "www.bamboohr.com" and parsed_final.path.rstrip("/") in {"", "/"}:
            raise BoardGoneError(
                "Sololearn BambooHR jobs board redirects to vendor root",
                url=final_url,
            )

        source_name = spec.source_name or "sololearn"
        tree = LexborHTMLParser(response.text)
        cards = tree.css(
            ".BambooHR-ATS-Jobs-Item, .BambooHR-ATS-Department-Item, "
            ".ResAts__listing, li:has(a[href*='/jobs/view.php'])"
        )
        for card in cards[: spec.limit or 50]:
            link = card.css_first("a[href]")
            if link is None:
                continue
            href = link.attributes.get("href", "")
            job_url = urljoin(final_url, href)
            title = _clean(link.text(separator=" ", strip=True))
            if not title or title.casefold() == "view job":
                heading = card.css_first("h2, h3, h4, .title, [class*='title']")
                title = _clean(heading.text(separator=" ", strip=True) if heading else "")
            if not title:
                continue
            body = _clean(card.text(separator=" ", strip=True))
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=job_url,
                url=job_url,
                text="\n".join(part for part in (title, body) if part),
                metadata={
                    "board_url": _BAMBOO_JOBS_URL,
                    "job_url": job_url,
                    "parser": "sololearn_bamboohr",
                },
            )


register_site_parser("sololearn", domain_pattern=SololearnParser.domain_pattern)(SololearnParser)
