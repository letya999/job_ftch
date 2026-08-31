"""Browser discovery for the official T2 vacancy board."""

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

_LISTING_URL = "https://t2.ru/about/career/vacancies"
_DETAIL_RE = re.compile(r"/vacancy/(\d+)(?:[/?#]|$)", re.IGNORECASE)


class T2CareerParser:
    domain_pattern = r"^https?://(?:careers\.)?t2\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True, wait="domcontentloaded", include_if_detail_page=False
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "t2_career"

    async def discover(self, spec: CareerSiteSpec, client: Any) -> list[str]:
        del client
        manifest_entry = getattr(self, "_manifest_entry", None)
        browser = getattr(manifest_entry, "browser", None)
        limit = spec.limit or getattr(manifest_entry, "limit", None) or 50
        bypass_strategy = spec.monitor_config.get("_bypass_strategy")
        browser_config = resolve_browser_config(spec, bypass_strategy)
        async with open_page(browser_config, bypass_strategy=bypass_strategy) as page:
            await navigate(page, _LISTING_URL, browser_config)
            current_url = getattr(page, "url", _LISTING_URL) or _LISTING_URL
            return await browser_scroll_collect_urls(
                page,
                current_url,
                _DETAIL_RE,
                limit=limit,
                scroll_loops=getattr(browser, "scroll_loops", None) or 6,
                pause_sec=(getattr(browser, "scroll_pause_ms", None) or 700) / 1000.0,
                scroll_px=getattr(browser, "scroll_px", None) or 2500,
            )

    @property
    def __name__(self) -> str:
        return "T2CareerParser"


register_site_parser(
    "t2_career",
    domain_pattern=T2CareerParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:t2.ru",
        has_stable_id=True,
        has_stable_url=True,
        rationale="Official T2 vacancy board exposes stable vacancy detail URLs through a browser-rendered listing.",
    ),
)(T2CareerParser)
