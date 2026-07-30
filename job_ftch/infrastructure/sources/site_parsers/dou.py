"""Runtime defaults for DOU jobs pages.

DOU listing pages expose stable vacancy detail URLs and publish dates in the
HTML/RSS surface, so the generic monitor stack benefits from a tight URL filter.
"""

from __future__ import annotations

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

_URL_FILTER = r"jobs\.dou\.ua/companies/[a-z0-9-]+/vacancies/\d+/?$"


class DouJobsParser:
    domain_pattern = r"^https?://jobs\.dou\.ua(?:/|$)"
    has_custom_parse = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(url_filter=_URL_FILTER, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @property
    def __name__(self) -> str:
        return "DouJobsParser"


register_site_parser(
    "dou_jobs",
    domain_pattern=DouJobsParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:jobs.dou.ua",
        has_stable_url=True,
        has_rss_or_sitemap_dates=True,
        can_detect_freshness_without_snapshot=True,
        ordered_by_newest=True,
        requires_full_snapshot=False,
        rationale="DOU jobs pages expose stable vacancy URLs plus listing dates/RSS signals and fit the generic monitor stack.",
    ),
)(DouJobsParser)
