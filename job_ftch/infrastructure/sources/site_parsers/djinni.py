"""Special parser for Djinni aggregator pages."""

from __future__ import annotations

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.infrastructure.sources.site_parsers.aggregator_boards import DjinniAggregatorParser

_DOMAIN_PATTERN = r"^https?://djinni\.co(?:/|$)"


class DjinniParser(DjinniAggregatorParser):
    domain_pattern = _DOMAIN_PATTERN
    parser_name = "djinni"


register_site_parser(
    "djinni",
    domain_pattern=DjinniParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:djinni.co",
        has_stable_url=True,
        rationale="Djinni exposes SSR job cards and stable detail URLs; the parser follows each detail and optional origin link.",
    ),
)(DjinniParser)
