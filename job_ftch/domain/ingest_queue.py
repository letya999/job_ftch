"""Durable continuation state for source runs interrupted by upstream limits."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IngestTaskState(StrEnum):
    READY = "ready"
    WAITING_RATE_LIMIT = "waiting_rate_limit"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_OPERATOR = "needs_operator"
    CANCELLED = "cancelled"


class IngestTask(BaseModel):
    """Safe, replayable source-run request.

    Browser sessions, cookies, credentials and operator attachments are
    intentionally absent. A queued task can therefore survive a restart.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    rate_scope: str = Field(min_length=1)
    state: IngestTaskState = IngestTaskState.READY
    attempt: int = Field(default=0, ge=0)
    max_items: int | None = Field(default=None, gt=0)
    user_id: str | None = None
    bypass_override: str | None = None
    parser_override: str | None = None
    personal_mode: bool = False
    trigger: str = Field(default="manual", min_length=1)
    available_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lease_owner: str | None = None
    lease_until: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


__all__ = ["IngestTask", "IngestTaskState"]
