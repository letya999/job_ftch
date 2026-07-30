"""Discover Maib's server-rendered career detail URLs."""

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


_DETAIL_PATH = re.compile(r"/maib/cariera/[^/?#]+$", re.IGNORECASE)


class MaibParser:
    """Select Maib detail pages whose slug has no generic URL signal."""

    domain_pattern = r"^https?://(?:www\.)?maib\.md(?:/|$)"
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
        base_url = str(response.url)
        seen: set[str] = set()
        urls: list[str] = []

        for anchor in HTMLParser(response.text).css("a[href]"):
            href = (anchor.attributes.get("href") or "").split("?", 1)[0]
            detail_url = urljoin(base_url, href)
            if not _DETAIL_PATH.search(detail_url) or detail_url in seen:
                continue
            seen.add(detail_url)
            urls.append(detail_url)
            if len(urls) >= (spec.limit or 50):
                break

        return urls


register_site_parser("maib", domain_pattern=MaibParser.domain_pattern)(MaibParser)
