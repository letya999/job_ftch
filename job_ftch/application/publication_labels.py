"""Explicit seniority in vacancy headings and source fields."""

from __future__ import annotations

import re

from job_ftch.domain.models import Seniority

_ALIASES = {
    "jr": "junior",
    "sr": "senior",
    "mid": "middle",
    "midlevel": "middle",
    "стажер": "intern",
    "стажёр": "intern",
    "джун": "junior",
    "джуниор": "junior",
    "мидл": "middle",
    "миддл": "middle",
    "сеньор": "senior",
    "сеньёр": "senior",
    "сениор": "senior",
    "синьор": "senior",
    "лід": "lead",
    "лид": "lead",
    "стажист": "intern",
}
_ALIASES.update({level.value: level.value for level in Seniority if level != Seniority.UNKNOWN})
_TERM = r"(?<!\w)(?:" + "|".join(_ALIASES) + r")(?!\w)"
_WORD = re.compile(_TERM, re.I)
_LEVEL = re.compile(
    r"(?:<dt>\s*(?:level|grade|seniority|грейд|уровень)\s*</dt>\s*<dd>\s*|"
    r"(?:^|[\n|;])\s*(?:level|grade|seniority|грейд|уровень)\s*[:=]\s*)([^<>\n;|.]{1,80})",
    re.I,
)
_SOURCE_HEADING = re.compile(
    r"(?:^|\n)\s*(?:job title|вакансия|позиция)\s*:\s*([^<>\n.;]{1,140})", re.I
)
_DESCRIPTION_LEVEL = re.compile(
    r"(?:^|\n)\s*job description\s*:\s*(" + _TERM + r"(?:\+?\s*[-–—/]\s*" + _TERM + r")*)", re.I
)


def _levels(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    value = re.split(
        r"\b(?:reporting to|reports to|assistant to|под руководством|в подчинении у)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0]
    title = re.sub(
        r"\b(?:middle (?:east|office)|lead generation|staff augmentation)\b", "", value, flags=re.I
    )
    return tuple(dict.fromkeys(_ALIASES[m[0].casefold()] for m in _WORD.finditer(title)))


def publication_grades(job: object, original: object) -> tuple[str, ...]:
    metadata = getattr(job, "metadata", {}) or {}
    source = original if isinstance(original, str) else ""
    selected: tuple[str, ...] = ()
    for value in (
        *(m[1] for m in _LEVEL.finditer(source)),
        metadata.get("seniority"),
        metadata.get("grade"),
        *(m[1] for m in _SOURCE_HEADING.finditer(source)),
        *(m[1] for m in _DESCRIPTION_LEVEL.finditer(source)),
        getattr(job, "title_raw", None),
        getattr(job, "title", None),
    ):
        levels = _levels(value)
        if levels and (not selected or set(selected) < set(levels)):
            selected = levels
    return selected
