"""Domain models and helpers for raw-item identity and deduplication."""

from __future__ import annotations

import re
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from job_ftch.domain.models import RawItem, SourceKind
from job_ftch.domain.observation import content_hash_for_raw_item

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#]+")
_DEDUP_URL_METADATA_KEYS = ("job_url", "canonical_url", "origin_url", "apply_url")
_TITLE_METADATA_KEYS = ("title", "job_title", "role")
_COMPANY_METADATA_KEYS = ("company", "company_name", "employer", "organization", "org")


def _hash_key(prefix: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _normalize_text(value: str) -> str:
    tokens = _TOKEN_RE.findall(value.casefold())
    return " ".join(tokens)


def processed_key_for_raw_item(item: RawItem) -> str:
    """Return the terminal-state key for one immutable content observation.

    A locator identifies where an item was found, not which version of its
    content was processed.  Including the full envelope hash lets a changed
    Telegram message or vacancy URL re-enter the pipeline while preserving
    suppression for an unchanged replay.
    """
    return _hash_key(
        "raw",
        str(item.source_kind),
        item.source_name.casefold(),
        str(item.stable_id),
        content_hash_for_raw_item(item),
    )


def processed_key_for_url(source_kind: SourceKind, source_name: str, url: str) -> str:
    """Return a locator-only discovery key, never a terminal processed key.

    Discovery happens before the current detail payload exists, so it cannot
    safely decide whether that payload is an already processed observation.
    Retained for callers that need a stable locator key for non-terminal caches.
    """
    return _hash_key("raw", str(source_kind), source_name.casefold(), url.casefold())


def dedup_url_for_raw_item(item: RawItem) -> str | None:
    for key in _DEDUP_URL_METADATA_KEYS:
        value = item.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    if item.source_kind is SourceKind.CAREER_SITE and item.url is not None:
        return str(item.url).strip().lower()
    return None


def dedup_title_for_raw_item(item: RawItem) -> str:
    for key in _TITLE_METADATA_KEYS:
        value = item.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)
    first_line = item.text.splitlines()[0] if item.text.splitlines() else item.text
    return _normalize_text(first_line)


def dedup_company_for_raw_item(item: RawItem) -> str:
    for key in _COMPANY_METADATA_KEYS:
        value = item.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)
    if item.source_kind is SourceKind.CAREER_SITE:
        return _normalize_text(item.source_name)
    return ""


def dedup_text_for_raw_item(item: RawItem) -> str:
    return _normalize_text(item.text)


def dedup_content_key_for_raw_item(item: RawItem) -> str:
    return _hash_key(
        "content",
        dedup_title_for_raw_item(item),
        dedup_company_for_raw_item(item),
        dedup_text_for_raw_item(item),
    )


def dedup_similarity_text_for_raw_item(item: RawItem) -> str:
    parts = [
        dedup_title_for_raw_item(item),
        dedup_company_for_raw_item(item),
        dedup_text_for_raw_item(item),
    ]
    return " ".join(part for part in parts if part)


class DedupKeyKind(StrEnum):
    URL = "url"
    CONTENT = "content"
    FINGERPRINT = "fingerprint"


class DuplicateRejectionReason(StrEnum):
    DUPLICATE_URL = "duplicate_url"
    DUPLICATE_CONTENT = "duplicate_content"
    DUPLICATE_NEAR_MATCH = "duplicate_near_match"


class RememberedDedupKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    kind: DedupKeyKind
    item_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    match_text: str | None = None
    url: str | None = None


class DuplicateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    reason: DuplicateRejectionReason
    duplicate_key: str = Field(min_length=1)
    matched_key: str = Field(min_length=1)
    matched_item_id: str = Field(min_length=1)
    matched_source_kind: SourceKind
    matched_source_name: str = Field(min_length=1)
    score: float | None = None
    details: str = Field(min_length=1)
