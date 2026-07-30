"""Browser parser for public Startup.jobs location and search listings."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_site_parser
from job_ftch.domain import SourceKind
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page, safe_content
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import resolve_browser_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_JOB_PATH = re.compile(r"^/(?!company/|locations/|markets/|remote-jobs$)(.+)-(\d+)$")


class StartupJobsParser:
    """Extract hydrated public job-detail links from Startup.jobs listings."""

    domain_pattern = r"^https?://(?:www\.)?startup\.jobs(?:/|$)"
    has_custom_parse = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @staticmethod
    def _items_from_html(html: str, spec: CareerSiteSpec) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        for link in LexborHTMLParser(html).css("a[href]"):
            href = urljoin("https://startup.jobs", link.attributes.get("href", ""))
            parsed = urlparse(href)
            if parsed.netloc not in {"startup.jobs", "www.startup.jobs"}:
                continue
            match = _JOB_PATH.fullmatch(parsed.path)
            title = " ".join(link.text(separator=" ", strip=True).split())
            detail_url = href.split("?", 1)[0]
            if match is None or not title or detail_url in seen:
                continue
            seen.add(detail_url)
            parent = link.parent
            card_text = (
                " ".join(parent.text(separator=" ", strip=True).split()) if parent else title
            )
            items.append(
                build_raw_item(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=spec.source_name or "startup_jobs",
                    external_id=match.group(2),
                    url=detail_url,
                    text=card_text if len(card_text) >= len(title) else title,
                    metadata={"board_url": spec.url, "parser": "startup_jobs_browser"},
                )
            )
        return items

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del client
        config = resolve_browser_config(spec, spec.monitor_config.get("_bypass_strategy"))
        async with open_page(
            config, bypass_strategy=spec.monitor_config.get("_bypass_strategy")
        ) as page:
            await navigate(page, str(spec.url), config)
            await page.wait_for_timeout(1500)
            html = await safe_content(page)
        for item in self._items_from_html(html, spec)[: spec.limit or 50]:
            yield item


register_site_parser("startupjobs", domain_pattern=StartupJobsParser.domain_pattern)(
    StartupJobsParser
)
