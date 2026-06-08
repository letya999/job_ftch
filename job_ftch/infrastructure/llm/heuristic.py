"""Deterministic extraction backend for local runs and tests."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_llm
from job_ftch.domain import WorkMode

if TYPE_CHECKING:
    from job_ftch.config import Settings

_TITLE_RE = re.compile(r"^[^\n:]{4,120}$")
_COMPENSATION_RE = re.compile(
    r"(?P<currency>USD|EUR|GBP|KZT|\$|€|£)\s*(?P<min>\d[\d\s]{2,})"
    r"(?:\s*(?:-|to|–)\s*(?P<max>\d[\d\s]{2,}))?",
    re.IGNORECASE,
)


def _normalize_amount(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _currency_code(value: str) -> str:
    normalized = value.upper()
    return {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
    }.get(normalized, normalized)


def _detect_work_mode(text: str) -> WorkMode | None:
    lowered = text.casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return None


class HeuristicLLMProvider:
    async def extract(self, text: str, schema: type[Any]) -> Any:
        lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
        title = next((line for line in lines if _TITLE_RE.match(line)), None)
        location = lines[1] if len(lines) > 1 else None
        compensation = None
        match = _COMPENSATION_RE.search(text)
        if match is not None:
            compensation = {
                "currency": _currency_code(match.group("currency")),
                "min_amount": _normalize_amount(match.group("min")),
                "max_amount": _normalize_amount(match.group("max")),
            }
        payload = {
            "title": title,
            "description": text.strip(),
            "location": location,
            "work_mode": _detect_work_mode(text),
            "compensation": compensation,
        }
        return schema.model_validate(payload)


@register_llm("heuristic")
def _build_heuristic_llm(settings: Settings) -> HeuristicLLMProvider:
    del settings
    return HeuristicLLMProvider()
