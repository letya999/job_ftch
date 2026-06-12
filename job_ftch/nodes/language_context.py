"""Language/context enrichment for raw items before semantic triage."""

from __future__ import annotations

import re

from job_ftch.domain import LanguageCode, RawItem

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _detect_language(text: str) -> LanguageCode:
    cyrillic = len(_CYRILLIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cyrillic == 0 and latin == 0:
        return LanguageCode.UNKNOWN
    if cyrillic >= latin:
        return LanguageCode.RU
    return LanguageCode.EN


class LanguageContextNode:
    async def process(self, item: RawItem) -> RawItem | None:
        language = _detect_language(item.text)
        metadata = {
            **item.metadata,
            "detected_language": language.value,
            "source_context": f"{item.source_kind.value}:{item.source_name.casefold()}",
        }
        return item.model_copy(update={"metadata": metadata})
