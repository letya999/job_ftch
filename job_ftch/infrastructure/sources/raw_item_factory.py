"""Helpers for constructing canonical RawItem payloads across source adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from job_ftch.domain import RawItem, SourceKind


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_raw_item(
    *,
    source_kind: SourceKind,
    source_name: str,
    external_id: str | None,
    text: str,
    url: str | None = None,
    created_at: datetime | None = None,
    fetched_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> RawItem:
    """Build the canonical RawItem shape shared by all phase-1 sources."""

    normalized_metadata = {
        key: value for key, value in (metadata or {}).items() if value is not None
    }
    payload: dict[str, Any] = {
        "source_kind": source_kind,
        "source_name": source_name,
        "external_id": external_id,
        "text": text,
        "created_at": _ensure_utc(created_at),
        "metadata": normalized_metadata,
    }
    if url is not None:
        payload["url"] = url
    if fetched_at is not None:
        payload["fetched_at"] = _ensure_utc(fetched_at)
    return RawItem.model_validate(payload)
