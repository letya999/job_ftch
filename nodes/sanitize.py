"""RawItem sanitation node for phase-0 and beyond."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, TypeAdapter

if TYPE_CHECKING:
    from domain import RawItem

_WHITESPACE_RE = re.compile(r"\s+")
_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


def _strip_control_chars(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char)[0] != "C" or char in "\n\t")


def _normalize_whitespace(value: str) -> str:
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "",
            parts.query,
            "",
        )
    )


class SanitizeNode:
    is_sanitize = True

    async def process(self, item: RawItem) -> RawItem | None:
        sanitized_text = _normalize_whitespace(_strip_control_chars(item.text))
        sanitized_source_name = _normalize_whitespace(_strip_control_chars(item.source_name))
        normalized_url = (
            _URL_ADAPTER.validate_python(_normalize_url(str(item.url))) if item.url is not None else None
        )
        updates: dict[str, object] = {
            "text": sanitized_text,
            "source_name": sanitized_source_name,
        }
        if normalized_url is not None:
            updates["url"] = normalized_url
        return item.model_copy(update=updates)
