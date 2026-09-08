"""Runtime defaults and search URL construction for hirehi.ru."""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    with_query_params,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_URL_FILTER = r"hirehi\.ru/[a-z0-9-]+/[a-z0-9-]+-\d+/?$"


def _job_posting(html_text: str) -> dict[str, Any] | None:
    for script in LexborHTMLParser(html_text).css('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.text())
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("@type") == "JobPosting":
            return value
    return None


class HireHiParser:
    """Read HireHi's server-rendered JSON-LD search surface."""

    domain_pattern = r"^https?://(?:www\.)?hirehi\.ru(?:/|$)"
    has_custom_parse = True
    terminal_on_empty = True
    supports_search = True
    search_mode = "per_keyword"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=_URL_FILTER,
            include_if_detail_page=True,
            extra={
                "pagination": {
                    "param_name": "page",
                    "start": 2,
                    "increment": 1,
                    "max_pages": 5,
                }
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        detail_re = re.compile(_URL_FILTER, re.IGNORECASE)
        seen: set[str] = set()
        page = 1
        while len(seen) < limit:
            parsed = urlparse(spec.url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if page > 1:
                query["page"] = str(page)
            page_url = urlunparse(parsed._replace(query=urlencode(query)))
            response = await client.get(page_url, follow_redirects=True)
            response.raise_for_status()
            base_url = str(getattr(response, "url", page_url) or page_url)
            found = 0
            for node in LexborHTMLParser(str(response.text)).css(
                'script[type="application/ld+json"]'
            ):
                try:
                    payload = json.loads(node.text())
                except (TypeError, json.JSONDecodeError):
                    continue
                elements = payload.get("itemListElement", []) if isinstance(payload, dict) else []
                if not isinstance(elements, list):
                    continue
                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    item = element.get("item") if isinstance(element.get("item"), dict) else element
                    if not isinstance(item, dict):
                        continue
                    url = urljoin(base_url, str(item.get("url") or ""))
                    if not detail_re.search(url) or url in seen:
                        continue
                    seen.add(url)
                    found += 1
                    try:
                        detail = await client.get(url, follow_redirects=True)
                        detail.raise_for_status()
                        posting = _job_posting(str(detail.text))
                    except Exception:  # noqa: BLE001 - preserve the listing fallback
                        posting = None
                    title = str((posting or item).get("title") or item.get("name") or "").strip()
                    description = " ".join(
                        LexborHTMLParser(
                            f"<div>{html.unescape(str((posting or {}).get('description') or ''))}</div>"
                        )
                        .text(separator=" ", strip=True)
                        .split()
                    )
                    if not title:
                        continue
                    yield build_raw_item(
                        source_kind=SourceKind.CAREER_SITE,
                        source_name=spec.source_name or "hirehi",
                        external_id=url.rstrip("/").rsplit("/", 1)[-1],
                        url=url,
                        text="\n".join(part for part in (title, description) if part),
                        metadata={
                            "parser": "hirehi",
                            "board_url": spec.url,
                            "detail_vacancy_confirmed": posting is not None,
                        },
                    )
                    if len(seen) >= limit:
                        return
            if found == 0:
                return
            page += 1

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
        if parsed.path.rstrip("/").startswith("/vacancies/"):
            parsed = parsed._replace(path="/")
        listing_url = urlunparse(parsed)
        return [with_query_params(listing_url, {"search": term}) for term in terms]

    @property
    def __name__(self) -> str:
        return "HireHiParser"


register_site_parser(
    "hirehi",
    domain_pattern=HireHiParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:hirehi.ru",
        has_stable_url=True,
        supports_ordered_head=True,
        can_detect_freshness_without_snapshot=False,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="HireHi exposes a server-rendered search query and JSON-LD vacancy links.",
    ),
)(HireHiParser)
