"""Helpers for constructing canonical RawItem payloads across source adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    RawItem,
    SourceFamily,
    SourceIdentity,
    SourceKind,
)


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
    source_identity: SourceIdentity | None = None,
) -> RawItem:
    """Build the canonical RawItem shape shared by all phase-1 sources."""

    normalized_metadata = {
        key: value for key, value in (metadata or {}).items() if value is not None
    }
    if source_identity is None:
        family = (
            SourceFamily.TELEGRAM
            if source_kind
            in {SourceKind.TELEGRAM_CHANNEL, SourceKind.TELEGRAM_GROUP, SourceKind.TELEGRAM_COMMENT}
            else SourceFamily.CAREER_WEB
            if source_kind is SourceKind.CAREER_SITE
            else SourceFamily.FIXTURE
        )
        kind_value = str(normalized_metadata.get("observation_kind") or "")
        try:
            observation_kind = ObservationKind(kind_value)
        except ValueError:
            observation_kind = (
                ObservationKind.VACANCY_DETAIL
                if normalized_metadata.get("detail_vacancy_confirmed") is True
                else ObservationKind.LISTING
                if normalized_metadata.get("board_url")
                else ObservationKind.COMMENT
                if source_kind is SourceKind.TELEGRAM_COMMENT
                else ObservationKind.MESSAGE
                if family is SourceFamily.TELEGRAM
                else ObservationKind.VACANCY_DETAIL
            )
        source_identity = SourceIdentity(
            family=family,
            observation_kind=observation_kind,
            transport=AcquisitionTransport.HTTP
            if family is not SourceFamily.TELEGRAM
            else AcquisitionTransport.TELEGRAM_API,
            adapter=str(normalized_metadata.get("adapter") or source_name),
            parser_version=str(normalized_metadata.get("parser_version") or "source-v1"),
            legacy_kind=str(source_kind),
        )
    payload: dict[str, Any] = {
        "source_kind": source_kind,
        "source_identity": source_identity,
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
