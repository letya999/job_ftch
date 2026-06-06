"""Domain models for quarantined raw input."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class RawItemRejectionReason(StrEnum):
    EMPTY_TEXT = "empty_text"
    EMPTY_SOURCE_NAME = "empty_source_name"
    MISSING_LOCATOR = "missing_locator"
    TEXT_TOO_LONG = "text_too_long"
    INVALID_URL = "invalid_url"
    INVALID_ORIGIN_URL = "invalid_origin_url"
    DISALLOWED_URL_HOST = "disallowed_url_host"
    DISALLOWED_ORIGIN_HOST = "disallowed_origin_host"
    PRIVATE_URL_HOST = "private_url_host"
    PRIVATE_ORIGIN_HOST = "private_origin_host"
    INVALID_RAW_ITEM = "invalid_raw_item"
    SOURCE_FETCH_ERROR = "source_fetch_error"
    NODE_FAILED = "node_failed"
    SINK_EMIT_ERROR = "sink_emit_error"
    SINK_FINALIZE_ERROR = "sink_finalize_error"


class QuarantinedRawItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: RawItemRejectionReason
    details: str = Field(min_length=1)
    source_kind: str | None = None
    source_name: str | None = None
    external_id: str | None = None
    url: AnyHttpUrl | None = None
    quarantined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot: dict[str, Any] = Field(default_factory=dict)
