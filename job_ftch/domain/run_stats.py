"""Relational projections of operator flags and per-run ingest stats."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceOperatorFlag(BaseModel):
    source_key: str
    important: bool = False
    set_by: str = "operator"
    set_at: str
    note: str | None = None

    model_config = ConfigDict(extra="forbid")


class PipelineRunStats(BaseModel):
    source_run_id: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int = 0
    source_count: int = 0
    ok_sources: int = 0
    fail_sources: int = 0
    fetched: int = 0
    extracted: int = 0
    emitted: int = 0
    review: int = 0
    rejected: int = 0
    dropped: int = 0
    failed: int = 0
    duplicates: int = 0
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_latency_ms: int = 0
    llm_cost_usd: float = 0.0
    conversion_extract: float = 0.0
    conversion_accept: float = 0.0
    extra_json: str = "{}"

    model_config = ConfigDict(extra="forbid")


class SourceRunStatsRow(BaseModel):
    source_run_id: str
    source_id: str
    source_key: str
    source_kind: str
    source_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    yielded: int = 0
    fetched: int = 0
    extracted: int = 0
    emitted: int = 0
    dropped: int = 0
    failed: int = 0
    duration_ms: int = 0
    llm_latency_ms: int = 0
    llm_cost_usd: float = 0.0
    conversion_accept: float = 0.0
    quality_reliable: bool = False
    quality_rich: bool = False
    quality_high_relevance: bool = False
    quality_important: bool = False
    error: str | None = None

    model_config = ConfigDict(extra="forbid")
