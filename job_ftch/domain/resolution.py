"""Durable deferred work records."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResolutionTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = ""
    observation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    required_claims: tuple[str, ...] = ()
    resolver_name: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    attempt: int = Field(default=0, ge=0)
    not_before: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, str] = Field(default_factory=dict)
    status: str = "pending"

    @model_validator(mode="after")
    def assign_task_id(self) -> ResolutionTask:
        if not self.task_id:
            raw = "|".join(
                (
                    self.observation_id,
                    self.candidate_id,
                    self.resolver_name,
                    self.policy_version,
                )
            )
            object.__setattr__(self, "task_id", sha256(raw.encode()).hexdigest())
        return self
