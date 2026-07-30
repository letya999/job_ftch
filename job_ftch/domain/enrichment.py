"""Durable, non-policy post-accept enrichment work."""

from __future__ import annotations

from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnrichmentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = ""
    observation_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    operations: tuple[str, ...] = ()
    policy_version: str = Field(min_length=1)
    status: str = "pending"

    @model_validator(mode="after")
    def assign_task_id(self) -> EnrichmentTask:
        if not self.task_id:
            raw = "|".join(
                (self.observation_id, self.group_id, self.policy_version, *self.operations)
            )
            object.__setattr__(self, "task_id", sha256(raw.encode()).hexdigest())
        return self
