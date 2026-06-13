"""Domain models for output job lineage and traceability."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from job_ftch.domain.models import JobRecord, ProvenanceTrail, SourceKind

if TYPE_CHECKING:
    from job_ftch.domain.job_group import JobGroup


class JobLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str | None = None
    job_id: str = Field(min_length=1)
    group_id: str | None = None
    raw_item_id: str = Field(min_length=1)
    source_record_id: str | None = None
    source_run_id: str | None = None
    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    source_url: AnyHttpUrl | None = None
    canonical_url: AnyHttpUrl | None = None
    fetched_at: datetime | None = None
    posted_at: datetime | None = None
    pipeline_stages: tuple[str, ...] = ()
    provenance: ProvenanceTrail = Field(default_factory=ProvenanceTrail)
    group_job_ids: tuple[str, ...] = ()
    group_source_count: int = 1


def build_job_lineage(
    job: JobRecord,
    *,
    tenant_id: str | None = None,
    group: JobGroup | None = None,
) -> JobLineage:
    stage_trace = [
        "source_fetch",
        "sanitize",
        "classification",
        "filtering",
        "dedup",
        "extraction",
        "extraction_validation",
        "normalization",
        "profile_matching",
        "risk_scoring",
        "quality_scoring",
        "job_validation",
    ]
    if job.group_id is not None or group is not None:
        stage_trace.append("aggregation")

    if group is None:
        group_job_ids: tuple[str, ...] = ()
        group_source_count = 1
    else:
        group_job_ids = tuple(member.job_id for member in group.jobs)
        group_source_count = group.source_count

    source_run_id = job.metadata.get("source_run_id")
    if not isinstance(source_run_id, str):
        source_run_id = None

    return JobLineage(
        tenant_id=tenant_id,
        job_id=job.job_id,
        group_id=job.group_id,
        raw_item_id=job.raw_item_id,
        source_record_id=job.source_record_id,
        source_run_id=source_run_id,
        source_kind=job.source_kind,
        source_name=job.source_name,
        source_url=job.source_url,
        canonical_url=job.canonical_url,
        fetched_at=job.fetched_at,
        posted_at=job.posted_at,
        pipeline_stages=tuple(stage_trace),
        provenance=job.provenance,
        group_job_ids=group_job_ids,
        group_source_count=group_source_count,
    )


JobLineage.model_rebuild()
