"""Structured records for dropped, quarantined, and failed pipeline items."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RejectedOutcome(StrEnum):
    DROPPED = "dropped"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class RejectedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: RejectedOutcome
    reason: str = Field(min_length=1)
    details: str = Field(min_length=1)
    stage: str | None = None
    item_type: str = Field(min_length=1)
    source_kind: str | None = None
    source_name: str | None = None
    stable_id: str | None = None
    raw_item_id: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot: dict[str, Any] = Field(default_factory=dict)
