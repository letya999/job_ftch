"""Company name normalization utilities."""

from __future__ import annotations

import re

LEGAL_SUFFIXES: tuple[str, ...] = (
    "ООО",
    "ПАО",
    "ЗАО",
    "АО",
    "АКБ",
    "ОАО",
    "НКО",
    "Ltd",
    "Inc",
    "LLC",
    "GmbH",
    "SRL",
    "S.A.",
    "Corp",
    "Co.",
)

_SUFFIX_PATTERN = re.compile(
    r"(?i)\s*\b(" + "|".join(re.escape(s) for s in LEGAL_SUFFIXES) + r")\b\.?\s*",
)
_WHITESPACE = re.compile(r"\s+")


def normalize_company_name(raw: str) -> str:
    """Strip legal suffixes, normalize whitespace, casefold."""
    cleaned = _SUFFIX_PATTERN.sub(" ", raw)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned.casefold()
