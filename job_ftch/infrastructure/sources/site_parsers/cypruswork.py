"""Discover-only parser for cypruswork.com."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    resolve_browser_config,
    safe_fetch,
)

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


class CyprusWorkParser:
    domain_pattern = r"(?:www\.)?cypruswork\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return "cypruswork"

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = getattr(manifest_entry, "detail_pattern", None) or r"/job/\d+/[a-z0-9\-]+/?$"
        return re.compile(str(pattern), re.IGNORECASE)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        detail_re = self._detail_re()
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        try:
            response = await safe_fetch(client, spec.url)
            current_url = str(response.url)
            if detail_re.search(current_url):
                return [current_url.split("?", 1)[0]]
            urls = _extract_detail_urls(
                str(response.text),
                current_url,
                limit=limit,
                detail_re=detail_re,
            )
            if urls:
                return urls
        except Exception:
            pass

        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            return await browser_scroll_collect_urls(
                page,
                str(getattr(page, "url", spec.url) or spec.url),
                detail_re,
                limit=limit,
                scroll_loops=2,
                pause_sec=0.5,
            )

    @property
    def __name__(self) -> str:
        return "CyprusWorkParser"


register_site_parser(
    "cypruswork",
    domain_pattern=CyprusWorkParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:cypruswork.com",
        has_stable_url=True,
        requires_full_snapshot=False,
        rationale="cypruswork.com exposes stable detail pages and is handled by a dedicated parser.",
    ),
)(CyprusWorkParser)
