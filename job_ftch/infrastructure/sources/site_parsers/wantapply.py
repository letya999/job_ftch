"""Public Wantapply listings with a TLS-profile fallback for its 403 edge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain.site_models import DiscoveredPostingPayload
from job_ftch.infrastructure.sources.scrapers.json_ld import parse_html
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_utils import payload_to_raw_item

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_HOSTS = frozenset({"wantapply.com", "www.wantapply.com"})


async def _fetch_html(url: str, client: Any, tls_client: Any) -> str:
    response = await client.get(url, follow_redirects=True)
    if response.status_code == 403:
        response = await tls_client.get(url, timeout=20)
    response.raise_for_status()
    return str(response.text)


def _detail_urls(html: str, base_url: str, limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for node in LexborHTMLParser(html).css("a[href]"):
        href = node.attributes.get("href") or ""
        url = urljoin(base_url, href).split("?", 1)[0].split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
            continue
        if "-at-" not in parsed.path or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


class WantapplyParser:
    domain_pattern = r"^https?://(?:www\.)?wantapply\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    confirmed_empty_on_empty = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str:
        del url
        return "wantapply"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        from curl_cffi.requests import AsyncSession

        base = "https://wantapply.com"
        # shortcut: the production tenant targets AI roles; add other categories when needed.
        listings = (
            [spec.url]
            if urlparse(spec.url).path.startswith("/jobs/")
            else [f"{base}/jobs/ai-engineer-prompt", f"{base}/jobs/machine-learning"]
        )
        limit = spec.limit or 20
        per_listing = (limit + len(listings) - 1) // len(listings)
        async with AsyncSession(impersonate="chrome") as tls_client:
            urls: list[str] = []
            for listing in listings:
                html = await _fetch_html(listing, client, tls_client)
                urls.extend(_detail_urls(html, listing, per_listing))
            for url in list(dict.fromkeys(urls))[:limit]:
                html = await _fetch_html(url, client, tls_client)
                parsed = parse_html(html, url=url)
                if parsed is None or not parsed.title or not parsed.description:
                    continue
                payload = DiscoveredPostingPayload(
                    url=url,
                    title=parsed.title,
                    description=parsed.description,
                    locations=parsed.locations,
                    employment_type=parsed.employment_type,
                    job_location_type=parsed.job_location_type,
                    date_posted=parsed.date_posted,
                    base_salary=parsed.base_salary,
                    language=parsed.language,
                    extras=parsed.extras,
                    metadata={**(parsed.metadata or {}), "detail_vacancy_confirmed": True},
                )
                yield payload_to_raw_item(payload, spec, spec.source_name or "wantapply")


register_site_parser(
    "wantapply",
    domain_pattern=WantapplyParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:wantapply.com",
        has_stable_url=True,
        supports_ordered_head=False,
        can_detect_freshness_without_snapshot=True,
        ordered_by_newest=False,
        requires_full_snapshot=False,
        rationale="Wantapply public category links lead to JobPosting detail pages.",
    ),
)(WantapplyParser)
