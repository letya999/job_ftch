"""HTTP listing parser for SuperJob Russia. Challenge pages stay terminal."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.monitors.shared import BrowserChallengeError
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    DEFAULT_LISTING_MAX_PAGES,
    is_challenge_response,
    keywords_from_spec,
    normalize_search_keywords,
    text_matches_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(
    r"(?:https?://(?:www\.)?superjob\.ru)?/vakansii/[a-z0-9-]+-(\d+)\.html",
    re.IGNORECASE,
)


@register_site_parser(
    "superjob_ru",
    domain_pattern=r"(?:[a-z0-9-]+\.)?superjob\.ru(?:/|$)",
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:superjob.ru"),
)
class SuperJobRuParser:
    """Parse listing HTML when it is real; raise on WAF instead of hanging."""

    domain_pattern = r"(?:[a-z0-9-]+\.)?superjob\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"
    confirmed_empty_on_empty = True

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        parsed = urlparse(base_url)
        path = parsed.path or "/vacancy/search/"
        if path.rstrip("/") in {"", "/vakansii"}:
            parsed = parsed._replace(path="/vacancy/search/")
        return [with_query_params(urlunparse(parsed), {"keywords": " OR ".join(terms)})]

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"superjob\.ru/vakansii/[a-z0-9-]+-\d+\.html$",
            include_if_detail_page=False,
            render=False,
            extra={
                "bypass_capability": "cloudflare_challenge",
                "bypass_capability_reason": "superjob_waf",
                "challenge_retries": 1,
                "persistent_context": True,
                "captcha_authorized_domains": ["www.superjob.ru", "superjob.ru"],
                "proxy_rescue_allow_domains": ["www.superjob.ru", "superjob.ru"],
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 5,
                },
            },
        )

    def parser_kind(self, url: str) -> None:
        del url
        return None

    def _items_from_html(
        self, html: str, board_url: str, source_name: str, keywords: list[str]
    ) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in HTMLParser(html).css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            match = _DETAIL_RE.search(href)
            if match is None:
                continue
            url = urljoin(board_url, href.split("?", 1)[0])
            if url in seen:
                continue
            seen.add(url)
            title = " ".join(anchor.text(separator=" ", strip=True).split())
            if len(title) < 3:
                continue
            if not text_matches_keywords(f"{title}\n{url}", keywords):
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=match.group(1),
                    url=url,
                    text=title,
                    metadata={"board_url": board_url, "parser": "superjob_ru"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        keywords = keywords_from_spec(spec)
        source_name = spec.source_name or "superjob_ru"
        limit = spec.limit or 50
        seen: set[str] = set()
        emitted = 0
        for index in range(DEFAULT_LISTING_MAX_PAGES):
            page_url = (
                spec.url if index == 0 else with_query_params(spec.url, {"page": str(index + 1)})
            )
            response = await client.get(page_url, follow_redirects=True)
            response.raise_for_status()
            html = str(response.text)
            if is_challenge_response(html):
                raise BrowserChallengeError(url=str(getattr(response, "url", page_url) or page_url))
            new_on_page = 0
            for item in self._items_from_html(html, spec.url, source_name, keywords):
                url_str = str(item.url or "")
                if not url_str or url_str in seen:
                    continue
                seen.add(url_str)
                new_on_page += 1
                yield item
                emitted += 1
                if emitted >= limit:
                    return
            if new_on_page == 0:
                return
