"""Discover-only parser for hireme.kz."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


def _extract_listing_detail_urls(html: str, base_url: str, *, limit: int) -> list[str]:
    tree = HTMLParser(html)
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in tree.css("a.mpp-title-link[href], a.mp-post-btn[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href).split("?", 1)[0]
        parsed = urlparse(absolute)
        if parsed.netloc != base_host or parsed.fragment:
            continue
        if absolute.rstrip("/") in {base_url.rstrip("/"), "https://hireme.kz"}:
            continue
        if any(
            token in parsed.path.lower()
            for token in ("/feed", "/wp-json", "/category/", "/tag/", "/job-openings")
        ):
            continue
        if not anchor.text(strip=True) or absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


class HiremeParser:
    domain_pattern = r"^https?://(?:www\.)?hireme\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        return _extract_listing_detail_urls(
            str(response.text),
            current_url,
            limit=spec.limit or 50,
        )

    @property
    def __name__(self) -> str:
        return "HiremeParser"


register_site_parser(
    "hireme",
    domain_pattern=HiremeParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hireme.kz",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="hireme.kz exposes detail pages via the AWSM Jobs listing page, but the slugs lack job keywords so the generic URL heuristics miss them.",
    ),
)(HiremeParser)
