"""Immutable source-observation identity for replay-safe processing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_ftch.domain.models import RawItem  # noqa: TC001 - Pydantic resolves the model at runtime.


def content_hash_for_raw_item(item: RawItem) -> str:
    """Hash the complete immutable raw-item envelope, not just its locator."""
    payload = item.model_dump(mode="json", exclude={"stable_id"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class ObservationLedgerEntry(BaseModel):
    """An append-only raw observation with separate content and decision versions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    stable_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    content_version: int = Field(default=1, ge=1)
    decision_version: str = Field(min_length=1)
    raw_item: RawItem
    source_cursor: str | None = None
    parent_observation_id: str | None = None
    context_observation_ids: tuple[str, ...] = ()
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_identity(self) -> ObservationLedgerEntry:
        if self.stable_id != self.raw_item.stable_id:
            raise ValueError("stable_id must match raw_item.stable_id")
        if self.content_hash != content_hash_for_raw_item(self.raw_item):
            raise ValueError("content_hash must match raw_item")
        return self
