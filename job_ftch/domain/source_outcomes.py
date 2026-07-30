from typing import Literal

SourceOutcome = Literal[
    "parsed_ok",
    "partial_with_items",
    "deadline_exceeded",
    "protected",
    "detail_extraction_failed",
    "listing_discovery_failed",
    "unconfirmed_empty",
    "no_open_vacancies",
    "board_gone",
    "failed",
]
