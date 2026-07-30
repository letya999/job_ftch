"""Source health domain model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceHealth(BaseModel):
    """Typed representation of a source's health and performance stats."""

    source_id: str
    source_kind: str
    source_name: str
    last_run_at: str
    last_started_at: str | None = None
    last_success_at: str | None
    failure_streak: int
    success_count: int
    last_fetched: int
    last_emitted: int
    last_failed: int
    last_quarantined: int
    baseline_emitted: float
    drift_ratio: float | None
    degraded: bool
    status: str
    paused: bool = False
    skipped_runs: int = 0
    last_eviction_at: str | None = None
    eviction_streak: int = 0
    last_eviction_kind: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    last_error_kind: str | None = None

    model_config = ConfigDict(extra="forbid")
