"""SSR parser for the official Kaspi Jumys vacancy board."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_DETAIL_RE = re.compile(r"^/a/[^/?#]+-(\d+)(?:/|$)", re.IGNORECASE)


def _clean(value: str) -> str:
    return " ".join(value.split())


class KaspiJumysParser:
    domain_pattern = r"^https?://jumys\.kaspi\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "kaspi_jumys_ssr"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        response = await safe_fetch(client, spec.url)
        page = LexborHTMLParser(response.text)
        seen: set[str] = set()
        emitted = 0
        for anchor in page.css("a[href]"):
            href = str(anchor.attributes.get("href") or "").strip()
            match = _DETAIL_RE.match(urlsplit(href).path)
            if match is None:
                continue
            parsed_url = urlsplit(urljoin(str(response.url or spec.url), href))
            url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
            if url in seen:
                continue
            seen.add(url)
            card = anchor
            for _ in range(4):
                parent = getattr(card, "parent", None)
                if parent is None:
                    break
                card = parent
                if "vacancy-listing-item" in str(card.attributes.get("class") or ""):
                    break
            text = _clean(card.text(separator="\n", strip=True))
            if not text:
                continue
            title = _clean(anchor.text(strip=True)) or text.split(" ", 1)[0]
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=spec.source_name or "kaspi_jumys_ssr",
                external_id=match.group(1),
                url=url,
                text="\n".join(part for part in (title, text) if part),
                metadata={
                    "board_url": spec.url,
                    "parser": "kaspi_jumys_ssr",
                    "observation_kind": "vacancy_detail",
                    "detail_vacancy_confirmed": True,
                },
            )
            emitted += 1
            if emitted >= limit:
                return


register_site_parser(
    "kaspi_jumys",
    domain_pattern=KaspiJumysParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:jumys.kaspi.kz",
        has_stable_id=True,
        has_stable_url=True,
        has_embedded_state=True,
        rationale="Official Kaspi Jumys SSR listing exposes stable vacancy links without sequential browser scraping.",
    ),
)(KaspiJumysParser)
