from typing import Literal

SourceOutcome = Literal[
    "parsed_ok",
    "partial_with_items",
    "deadline_exceeded",
    "protected",
    "waf_challenge",
    "provider_tunnel_denied",
    "soft_403_with_content",
    "stale_url",
    "parser_gap",
    "detail_extraction_failed",
    "listing_discovery_failed",
    "unconfirmed_empty",
    "no_open_vacancies",
    "board_gone",
    "failed",
]
