"""Runtime-managed source records and stable source identity helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import SourceSpec

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str, *, default: str = "source") -> str:
    normalized = _NON_ALNUM_RE.sub("_", value.casefold()).strip("_")
    return normalized or default


def _short_hash(value: str) -> str:
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def source_spec_name(spec: SourceSpec) -> str:
    explicit = getattr(spec, "source_name", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    locator = source_spec_locator(spec)
    if locator is None:
        return _slugify(spec.type)
    if spec.type.startswith("telegram"):
        return _slugify(locator.removeprefix("@"))
    if spec.type == "local_fixture":
        return _slugify(Path(locator).stem)
    if spec.type == "lever":
        return _slugify(locator)

    parsed = urlsplit(locator)
    if parsed.scheme and parsed.netloc:
        base = _slugify(parsed.netloc)
        suffix_parts = [part for part in parsed.path.split("/") if part]
        suffix = _slugify("-".join(suffix_parts), default="")
        if suffix:
            base = f"{base}_{suffix}"
        if parsed.query:
            base = f"{base}_{_short_hash(parsed.query)}"
        return base
    return _slugify(locator)


def source_spec_identifier(spec: SourceSpec) -> str:
    return f"{spec.type}:{source_spec_name(spec)}"


def source_spec_locator(spec: SourceSpec) -> str | None:
    for field_name in ("entity", "url", "feed_url", "base_url", "path", "company"):
        value = getattr(spec, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


class RuntimeSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    spec: SourceSpec
    enabled: bool = True
    origin: Literal["runtime"] = "runtime"
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    added_via: str = "runtime"
    added_by: str | None = None
    input_value: str | None = None


RuntimeSourceRecord.model_rebuild(
    _types_namespace={"SourceSpec": import_module("job_ftch.domain.source_spec").SourceSpec}
)
