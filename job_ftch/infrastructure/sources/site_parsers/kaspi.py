"""Discover-only parser for job.kaspi.kz."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.browser_utils import navigate, open_page
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    browser_scroll_collect_urls,
    resolve_browser_config,
)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


class KaspiParser:
    domain_pattern = r"(?:www\.)?job\.kaspi\.kz(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="networkidle",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "kaspi"

    def _detail_re(self) -> re.Pattern[str]:
        manifest_entry = getattr(self, "_manifest_entry", None)
        pattern = getattr(manifest_entry, "detail_pattern", None) or r"/vacancy/[^/]+"
        return re.compile(str(pattern), re.IGNORECASE)

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        del client
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        detail_re = self._detail_re()
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, spec.url, browser_config)
            current_url = getattr(page, "url", spec.url) or spec.url
            if detail_re.search(current_url):
                return [current_url.split("?", 1)[0]]
            browser = getattr(getattr(self, "_manifest_entry", None), "browser", None)
            return await browser_scroll_collect_urls(
                page,
                current_url,
                detail_re,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 5,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 500) / 1000.0,
            )

    @property
    def __name__(self) -> str:
        return "KaspiParser"


register_site_parser(
    "kaspi",
    domain_pattern=KaspiParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:job.kaspi.kz",
        has_stable_url=True,
        has_embedded_state=True,
        requires_full_snapshot=False,
        rationale="job.kaspi.kz uses a dedicated SPA parser with stable vacancy detail URLs.",
    ),
)(KaspiParser)
