"""Runtime defaults for Djinni listing pages.

Djinni exposes stable detail URLs under ``/jobs/<id>-<slug>/`` and works with
the generic career-site monitor stack. This parser only narrows discovery.
"""

from __future__ import annotations

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

_URL_FILTER = r"djinni\.co/jobs/\d+-[a-z0-9-]+/?$"


class DjinniParser:
    domain_pattern = r"^https?://djinni\.co/jobs(?:/|$)"
    has_custom_parse = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(url_filter=_URL_FILTER, include_if_detail_page=False)

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    @property
    def __name__(self) -> str:
        return "DjinniParser"


register_site_parser(
    "djinni",
    domain_pattern=DjinniParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:djinni.co",
        has_stable_url=True,
        rationale="Djinni exposes stable job detail URLs and is best ingested through the generic monitor stack.",
    ),
)(DjinniParser)
