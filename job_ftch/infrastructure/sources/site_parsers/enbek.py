"""Server-rendered listing parser for the Enbek public vacancy board."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(r"/[a-z]{2}/vacancy/[^/?#]+~(\d+)/?$", re.IGNORECASE)
_URL_FILTER = r"enbek\.kz/[a-z]{2}/vacancy/[^/?#]+~\d+"


class EnbekParser:
    domain_pattern = r"^https?://(?:www\.)?enbek\.kz(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            include_if_detail_page=False,
            url_filter=_URL_FILTER,
            extra={
                "proxy_rescue_allow_domains": ["enbek.kz", "www.enbek.kz"],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, page_url: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for anchor in LexborHTMLParser(html).css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            url = urljoin(page_url, href).split("?", 1)[0].split("#", 1)[0]
            match = _DETAIL_RE.search(url)
            if match is None or url in seen:
                continue
            seen.add(url)
            title = " ".join(anchor.text(strip=True).split())
            parent = anchor.parent
            body = (
                " ".join(parent.text(separator=" ", strip=True).split())
                if parent is not None
                else title
            )
            if not title:
                title = body[:120]
            if not title:
                continue
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "enbek",
                    external_id=match.group(1),
                    url=url,
                    text=f"{title}\n{body}" if body and body != title else title,
                    metadata={
                        "board_url": spec.url,
                        "parser": "enbek_listing",
                        "detail_vacancy_confirmed": False,
                    },
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        response = await client.get(spec.url, follow_redirects=True)
        response.raise_for_status()
        for item in self._items_from_html(response.text, str(response.url), spec)[
            : spec.limit or 50
        ]:
            yield item


register_site_parser(
    "enbek",
    domain_pattern=EnbekParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:enbek.kz",
        has_stable_url=True,
        has_stable_id=True,
        supports_ordered_head=True,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="enbek.kz search listings are server-rendered with stable /vacancy/{slug}~{id} URLs.",
    ),
)(EnbekParser)
