"""Prevent HTEC career marketing pages from being emitted as vacancies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import safe_fetch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_BOARD_URL = "https://htec.com/careers/jobs/"


class HtecParser:
    """Treat the dedicated board as authoritative, never career-content links."""

    domain_pattern = r"^https?://(?:www\.)?htec\.com/careers(?:/|$)"
    has_custom_parse = True
    terminal_on_empty = True

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(self, spec: CareerSiteSpec, client: Any) -> AsyncIterator[RawItem]:
        del spec
        try:
            await safe_fetch(client, _BOARD_URL)
        except httpx.HTTPStatusError:
            return
        # A successful board is intentionally not treated as a job unless it
        # exposes explicit vacancy data.  This keeps career articles out.
        return
        yield  # pragma: no cover


register_site_parser("htec", domain_pattern=HtecParser.domain_pattern)(HtecParser)
