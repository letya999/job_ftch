"""Tenant configuration models for multi-tenant deployments."""

from __future__ import annotations

import re
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import SourceSpec

_TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str | None = None
    path: Path | None = None
    table: str | None = None
    format: str | None = None
    jsonl: bool = False
    schema_version: str | None = "job_ftch.job.v1"

    def render_path(self, tenant_id: str) -> Path | None:
        if self.path is None:
            return None
        return Path(str(self.path).format(tenant_id=tenant_id))


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_seconds: int | None = Field(default=None, gt=0)
    ingest_mode: str | None = None

    @field_validator("ingest_mode")
    @classmethod
    def strip_ingest_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=63)
    display_name: str = Field(min_length=1)
    sources: list[SourceSpec] = Field(default_factory=list)
    output: OutputSpec = Field(
        default_factory=lambda: OutputSpec(path=Path("artifacts/{tenant_id}/jobs.json"))
    )
    schedule: ScheduleSpec | None = None
    auth_provider: str | None = None
    filter_profile_path: Path | None = None
    quarantine_output: OutputSpec = Field(
        default_factory=lambda: OutputSpec(
            path=Path("artifacts/{tenant_id}/quarantine.jsonl"),
            jsonl=True,
            schema_version="job_ftch.quarantine.v1",
        )
    )
    review_output: OutputSpec = Field(
        default_factory=lambda: OutputSpec(
            path=Path("artifacts/{tenant_id}/review.jsonl"),
            jsonl=True,
        )
    )
    rejected_output: OutputSpec = Field(
        default_factory=lambda: OutputSpec(
            path=Path("artifacts/{tenant_id}/rejected.jsonl"),
            jsonl=True,
            schema_version="job_ftch.rejected.v1",
        )
    )
    source_backend: str = "local_fixture"
    # Backend selection defaults to None so that "the tenant did not say" stays
    # distinguishable from "the tenant asked for the default"; a non-None value
    # here outranks env and runtime YAML, so it must be an explicit choice.
    sink_backend: str | None = None
    store_backend: str | None = None
    job_group_store_backend: str | None = None
    posting_backend: str | None = None
    notify_mode: str = "instant"
    notify_batch_size: int = 10
    dry_run: bool = False
    pipeline_max_items_per_run: int | None = Field(default=None, gt=0)
    source_fetch_concurrency: int | None = None
    source_fetch_concurrency_adaptive: bool | None = None
    source_preparation_concurrency: int | None = None
    source_preparation_concurrency_adaptive: bool | None = None
    pipeline_item_concurrency: int | None = None
    pipeline_item_concurrency_adaptive: bool | None = None
    pipeline_max_text_length: int = 20_000
    review_max_quality_score: float = 0.65
    posting_min_quality_score: float = 0.8
    routing_accept_threshold: float = 0.85
    routing_review_threshold: float = 0.5
    routing_quality_override_threshold: float = 0.6
    store_path: Path = Path(".runtime/{tenant_id}/job_ftch.db")
    job_store_path: Path | None = None
    store_pool_min: int = 2
    store_pool_max: int = 10
    store_fallback_on_error: bool = True
    memory_max_keys: int = 50_000
    memory_max_set_members: int = 50_000
    job_backend: str = "sqlite"
    search_backend: str | None = None
    vector_backend: str | None = None
    language_detection_enabled: bool = False
    search_language: str = "simple"

    # Deprecated: model and LLM selection is runtime-YAML policy, not tenant
    # identity. These keys are still parsed so existing tenant files load, but
    # they are ignored unless allow_tenant_model_override is set explicitly.
    llm_backend: str | None = None
    openai_model: str | None = None
    relevance_llm_model: str | None = None
    embedding_enabled: bool | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    allow_tenant_model_override: bool = False

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _TENANT_ID_RE.fullmatch(normalized):
            msg = "tenant_id must be a slug containing lowercase letters, digits, _ or -."
            raise ValueError(msg)
        return normalized

    @field_validator(
        "display_name",
        "auth_provider",
        "source_backend",
        "sink_backend",
        "store_backend",
        "job_group_store_backend",
        "llm_backend",
        "posting_backend",
        "job_backend",
        "search_backend",
        "vector_backend",
        "embedding_provider",
        "embedding_model",
        "openai_model",
        "relevance_llm_model",
        "search_language",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


class TenantInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    display_name: str
    source_count: int
    last_run: dict[str, object] | None = None


TenantConfig.model_rebuild(
    _types_namespace={"SourceSpec": import_module("job_ftch.domain.source_spec").SourceSpec}
)
