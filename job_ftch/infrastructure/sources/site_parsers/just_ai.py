"""WordPress REST parser for careers.just-ai.com."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from selectolax.parser import HTMLParser

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    keywords_from_spec,
    safe_fetch,
    text_matches_keywords,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec

_API_URL = "https://careers.just-ai.com/wp-json/wp/v2/vacancy"


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    fragment = HTMLParser(f"<div>{html.unescape(value)}</div>")
    if fragment.body is None:
        return " ".join(html.unescape(value).split())
    return " ".join(fragment.body.text(separator=" ", strip=True).split())


class JustAICareerParser:
    domain_pattern = r"^https?://(?:www\.)?careers\.just-ai\.com(?:/|$)"
    has_custom_parse = True
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(render=False, wait="domcontentloaded")

    def parser_kind(self, url: str) -> str | None:
        del url
        return "just_ai"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        limit = spec.limit or 50
        source_name = spec.source_name or "just_ai"
        keywords = keywords_from_spec(spec)
        emitted = 0
        page = 1
        per_page = min(limit, 50)
        seen: set[str] = set()
        while emitted < limit:
            response = await safe_fetch(client, f"{_API_URL}?per_page={per_page}&page={page}")
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                return
            page_emitted = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                raw_title = row.get("title")
                title_block = raw_title if isinstance(raw_title, dict) else {}
                title = _strip_html(str(title_block.get("rendered") or "").strip())
                url = str(row.get("link") or "").strip()
                external_id = str(row.get("slug") or row.get("id") or url)
                if not title or not url or external_id in seen:
                    continue
                seen.add(external_id)
                raw_content = row.get("content")
                content_block = raw_content if isinstance(raw_content, dict) else {}
                body = _strip_html(str(content_block.get("rendered") or ""))
                if not body:
                    try:
                        detail = await safe_fetch(client, url)
                        body = _strip_html(detail.text)
                    except Exception:  # noqa: BLE001 - title is enough to emit
                        body = ""
                text = "\n".join(part for part in (title, body) if part)
                if not text_matches_keywords(text, keywords):
                    continue
                yield build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=external_id,
                    url=url,
                    text=text,
                    metadata={
                        "board_url": spec.url,
                        "parser": "just_ai",
                        "company": "Just AI",
                        "company_authoritative": True,
                        "detail_vacancy_confirmed": bool(body),
                    },
                )
                emitted += 1
                page_emitted += 1
                if emitted >= limit:
                    return
            headers = getattr(response, "headers", None) or {}
            total_pages = str(
                headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages") or ""
            )
            if total_pages.isdigit() and page >= int(total_pages):
                return
            if len(rows) < per_page or page_emitted == 0:
                return
            page += 1


register_site_parser(
    "just_ai",
    domain_pattern=JustAICareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:just-ai"),
)(JustAICareerParser)
