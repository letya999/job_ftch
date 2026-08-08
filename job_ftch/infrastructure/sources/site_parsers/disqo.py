"""Parser for DISQO careers lazy Lever shortcode."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import is_challenge_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_CAREERS_URL = "https://www.disqo.com/careers/"
_NONCE_RE = re.compile(r'formData\.append\("nonce",\s*"([^"]+)"\)')
_TAGS_RE = re.compile(r'formData\.append\("tags",\s*"([^"]+)"\)')
_AJAX_RE = re.compile(r"fetch\('([^']*admin-ajax\.php)'")


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _job_id(url: str) -> str:
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return tail or url


class DisqoParser:
    """Resolve NitroPack lazy shortcode and parse Lever job cards."""

    domain_pattern = r"^https?://(?:www\.)?disqo\.com/careers(?:/|$)"
    has_custom_parse = True
    terminal_on_empty = True
    confirmed_empty_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        page = await client.get(_CAREERS_URL, follow_redirects=True)
        if page.status_code >= 400 or is_challenge_response(page.text):
            page.raise_for_status()

        nonce = _NONCE_RE.search(page.text)
        tags = _TAGS_RE.search(page.text)
        ajax = _AJAX_RE.search(page.text)
        if not nonce or not tags or not ajax:
            return

        response = await client.post(
            ajax.group(1),
            data={
                "action": "nitro_shortcode_ajax",
                "nonce": nonce.group(1),
                "tags": tags.group(1),
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": _CAREERS_URL,
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        shortcode_html = ""
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            shortcode_html = "\n".join(str(value) for value in data.values())
        if not shortcode_html:
            return

        source_name = spec.source_name or "disqo"
        tree = LexborHTMLParser(shortcode_html)
        for card in tree.css(".ljb-job-card")[: spec.limit or 50]:
            title = _clean(
                card.css_first(".ljb-job-title").text() if card.css_first(".ljb-job-title") else ""
            )
            link = card.css_first("a[href]")
            job_url = link.attributes.get("href", "") if link else ""
            if not title or not job_url:
                continue
            department = _clean(
                card.css_first(".ljb-job-dept").text() if card.css_first(".ljb-job-dept") else ""
            )
            location = _clean(
                card.css_first(".ljb-job-location").text()
                if card.css_first(".ljb-job-location")
                else ""
            )
            commitment = _clean(
                card.css_first(".ljb-job-commitment").text()
                if card.css_first(".ljb-job-commitment")
                else ""
            )
            salary = _clean(
                card.css_first(".ljb-job-salary").text()
                if card.css_first(".ljb-job-salary")
                else ""
            )
            yield build_raw_item(
                source_kind=SourceKind.CAREER_SITE,
                source_name=source_name,
                external_id=_job_id(job_url),
                url=job_url,
                text="\n".join(
                    part for part in (title, department, location, commitment, salary) if part
                ),
                metadata={
                    "board_url": _CAREERS_URL,
                    "job_url": job_url,
                    "department": department or None,
                    "location": location or None,
                    "commitment": commitment or None,
                    "salary": salary or None,
                    "parser": "disqo_nitro_lever_shortcode",
                },
            )


register_site_parser("disqo", domain_pattern=DisqoParser.domain_pattern)(DisqoParser)
