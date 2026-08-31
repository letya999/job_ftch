"""Authoritative Next.js vacancy listing for Gazprombank Tech."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


def _extract_listing(html: str, base_url: str) -> tuple[int, list[str]]:
    node = HTMLParser(html).css_first("#__NEXT_DATA__")
    if node is None:
        raise ValueError("Gazprombank __NEXT_DATA__ is missing")
    payload = json.loads(node.text())
    listing = payload["props"]["pageProps"]["json"]
    vacancies = listing.get("vacancies")
    total = listing.get("total")
    if not isinstance(vacancies, list) or not isinstance(total, int):
        raise ValueError("Gazprombank vacancy listing has an unknown shape")
    urls: list[str] = []
    for vacancy in vacancies:
        if not isinstance(vacancy, dict):
            continue
        value = next(
            (vacancy.get(key) for key in ("url", "href", "link", "slug") if vacancy.get(key)),
            None,
        )
        if isinstance(value, str):
            urls.append(urljoin(base_url, value))
    if total and not urls:
        raise ValueError("Gazprombank reports vacancies without detail URLs")
    return total, list(dict.fromkeys(urls))


class GazprombankTechParser:
    domain_pattern = r"^https?://(?:www\.)?gazprombank\.tech/vacancies(?:[/?#]|$)"
    has_custom_parse = True
    supports_discover = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        response = await client.get(spec.url, follow_redirects=True)
        response.raise_for_status()
        _total, urls = _extract_listing(response.text, str(response.url))
        return urls[: spec.limit or 50]

    @property
    def __name__(self) -> str:
        return "GazprombankTechParser"


register_site_parser(
    "gazprombank_tech",
    domain_pattern=GazprombankTechParser.domain_pattern,
)(GazprombankTechParser)
