"""Generic-pipeline defaults for SuperJob Russia vacancy URLs."""

from __future__ import annotations

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults


@register_site_parser(
    "superjob_ru",
    domain_pattern=r"(?:[a-z0-9-]+\.)?superjob\.ru(?:/|$)",
    assessment_hint=known_board_assessment_hint("known_site", "site_parser:superjob.ru"),
)
class SuperJobRuParser:
    """Keep the generic monitor/scraper and admit only numeric detail URLs."""

    domain_pattern = r"(?:[a-z0-9-]+\.)?superjob\.ru(?:/|$)"
    has_custom_parse = False
    supports_discover = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            url_filter=r"superjob\.ru/vakansii/[a-z0-9-]+-\d+\.html$",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> None:
        del url
        return None
