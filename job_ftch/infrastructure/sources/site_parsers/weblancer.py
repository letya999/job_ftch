"""Discover-only parser for weblancer.net."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


def _extract_detail_urls(
    html: str,
    base_url: str,
    *,
    limit: int,
    detail_re: re.Pattern[str],
) -> list[str]:
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href or not detail_re.search(href):
            continue
        absolute = urljoin(base_url, href.split("?", 1)[0])
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


class WeblancerParser:
    domain_pattern = r"^https?://(?:www\.)?weblancer\.net(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = (
            getattr(manifest_entry, "detail_pattern", None) or r"/freelance/[^/]+/[^/]+-\d+/?$"
        )
        return re.compile(str(pattern), re.IGNORECASE)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        current_url = str(response.url)
        detail_re = self._detail_re()
        if detail_re.search(current_url):
            return [current_url.split("?", 1)[0]]
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        return _extract_detail_urls(
            str(response.text),
            current_url,
            limit=limit,
            detail_re=detail_re,
        )

    @property
    def __name__(self) -> str:
        return "WeblancerParser"


register_site_parser(
    "weblancer",
    domain_pattern=WeblancerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:weblancer.net",
        source_family="freelance_board",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="weblancer.net renders canonical /freelance/{category}/{slug}-{id}/ links in SSR HTML, so a dedicated parser avoids the generic detail extraction misses on the Next.js shell.",
    ),
)(WeblancerParser)
