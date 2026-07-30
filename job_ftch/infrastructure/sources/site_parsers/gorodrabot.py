"""Discover stable vacancy details on GorodRabот Belarus."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


_DETAIL_RE = re.compile(r"/advert/\d+/[a-z0-9_\-]+/?$", re.IGNORECASE)


class GorodRabotParser:
    domain_pattern = r"^https?://(?:[a-z-]+\.)?gorodrabot\.by(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=True)

    def parser_kind(self, url: str) -> str | None:
        del url
        return "gorodrabot"

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await safe_fetch(client, spec.url)
        base_url = str(response.url)
        seen: set[str] = set()
        urls: list[str] = []
        for anchor in HTMLParser(response.text).css("a[href]"):
            href = anchor.attributes.get("href")
            if not href:
                continue
            url = urljoin(base_url, href.split("?", 1)[0])
            if not _DETAIL_RE.search(url) or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= (spec.limit or 50):
                break
        return urls


register_site_parser("gorodrabot", domain_pattern=GorodRabotParser.domain_pattern)(GorodRabotParser)
