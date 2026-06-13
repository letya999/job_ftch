"""Domain models for cross-source job aggregation."""

from __future__ import annotations

import string
from datetime import datetime  # noqa: TC003
from hashlib import sha256
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from .models import JobExtractionStatus, JobRecord, ProvenanceTrail, RiskLevel, SourceKind, WorkMode


class SourceAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: SourceKind
    source_name: str
    url: AnyHttpUrl | None
    first_seen_at: datetime
    last_seen_at: datetime


class JobGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str  # sha256 of canonical fingerprint
    canonical_job: JobRecord  # merged best-field record
    jobs: list[JobRecord]  # one per unique source (ordered by source priority)
    source_attributions: list[SourceAttribution]
    source_count: int  # = len(jobs)
    first_seen_at: datetime
    last_seen_at: datetime
    blocking_key: str | None = None
    merge_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    lifecycle_status: str = "active"


def compute_group_id(job: JobRecord) -> str:
    """
    Computes a unique group_id for a job.
    If canonical_url is present, it's used as the primary identifier.
    Otherwise, uses the semantic identity fingerprint.
    """
    if job.canonical_url:
        return sha256(str(job.canonical_url).encode("utf-8")).hexdigest()
    return compute_identity_fingerprint(job)


def compute_identity_fingerprint(job: JobRecord) -> str:
    """
    Computes a semantic identity fingerprint (company + title + location).
    Used for cross-source matching when URLs differ.
    """

    def normalize_title(title: str | None) -> str:
        if not title:
            return ""
        # lowercase, strip punctuation, sort words
        t = title.lower()
        t = t.translate(str.maketrans("", "", string.punctuation))
        words = sorted(t.split())
        return " ".join(words)

    parts = [
        (job.company_canonical or job.company or "").lower().strip(),
        normalize_title(job.title),
        (job.location or "").lower().strip(),
    ]
    fingerprint_raw = "|".join(parts)
    return sha256(fingerprint_raw.encode("utf-8")).hexdigest()


def merge_jobs(jobs: list[JobRecord]) -> JobRecord:
    """
    Merges multiple Job objects into one canonical record.
    Priority order: CAREER_SITE > TELEGRAM_CHANNEL > TELEGRAM_GROUP > TELEGRAM_COMMENT > DEBUG
    """
    if not jobs:
        raise ValueError("Cannot merge empty list of jobs.")

    # Sort jobs by source priority
    priority = {
        SourceKind.CAREER_SITE: 0,
        SourceKind.TELEGRAM_CHANNEL: 1,
        SourceKind.TELEGRAM_GROUP: 2,
        SourceKind.TELEGRAM_COMMENT: 3,
        SourceKind.DEBUG: 4,
    }

    def get_priority(j: JobRecord) -> int:
        return priority.get(j.source_kind, 99)

    sorted_jobs = sorted(jobs, key=get_priority)
    canonical = sorted_jobs[0]

    # canonical_url: first non-None from highest-priority source
    canonical_url = next((j.canonical_url for j in sorted_jobs if j.canonical_url), None)

    # title, company, company_canonical, location, work_mode: from highest-priority source that has it
    title = next((j.title for j in sorted_jobs if j.title), canonical.title)
    company = next((j.company for j in sorted_jobs if j.company), canonical.company)
    company_canonical = next(
        (j.company_canonical for j in sorted_jobs if j.company_canonical),
        canonical.company_canonical,
    )
    location = next((j.location for j in sorted_jobs if j.location), canonical.location)

    # work_mode: from highest-priority source that is NOT UNKNOWN
    work_mode = next(
        (j.work_mode for j in sorted_jobs if j.work_mode != WorkMode.UNKNOWN), canonical.work_mode
    )

    # description: longest non-empty description wins
    description = max(
        (j.description for j in sorted_jobs if j.description),
        key=len,
        default=canonical.description,
    )

    # compensation: first non-None value
    compensation = next(
        (j.compensation for j in sorted_jobs if j.compensation),
        canonical.compensation,
    )

    source_url = next((j.source_url for j in sorted_jobs if j.source_url), canonical.source_url)
    fetched_candidates = [j.fetched_at for j in jobs if j.fetched_at is not None]
    fetched_at = max(fetched_candidates) if fetched_candidates else canonical.fetched_at
    posted_candidates = [j.posted_at for j in jobs if j.posted_at is not None]
    posted_at = min(posted_candidates) if posted_candidates else canonical.posted_at

    # quality_score, relevance_score: max of all
    quality_scores = [j.quality_score for j in jobs if j.quality_score is not None]
    quality_score = max(quality_scores) if quality_scores else None

    relevance_scores = [j.relevance_score for j in jobs if j.relevance_score is not None]
    relevance_score = max(relevance_scores) if relevance_scores else None

    risk_scores = [j.risk_score for j in jobs if j.risk_score is not None]
    risk_score = max(risk_scores) if risk_scores else None
    risk_level = None
    if any(j.risk_level == RiskLevel.HIGH for j in jobs):
        risk_level = RiskLevel.HIGH
    elif any(j.risk_level == RiskLevel.MEDIUM for j in jobs):
        risk_level = RiskLevel.MEDIUM
    elif any(j.risk_level == RiskLevel.LOW for j in jobs):
        risk_level = RiskLevel.LOW

    # extraction_status: COMPLETE if any source has it, else PARTIAL
    extraction_status = (
        JobExtractionStatus.COMPLETE
        if any(j.extraction_status == JobExtractionStatus.COMPLETE for j in jobs)
        else JobExtractionStatus.PARTIAL
    )

    # review_reasons: union of all unique reasons
    all_reasons: set[str] = set()
    for j in jobs:
        all_reasons.update(j.review_reasons)
    review_reasons = tuple(sorted(list(all_reasons)))

    # metadata: merged dict (higher-priority source wins on key conflict)
    metadata: dict[str, Any] = {}
    for j in reversed(sorted_jobs):  # lower priority first so higher priority overwrites
        metadata.update(j.metadata)

    provenance = ProvenanceTrail(
        extraction=tuple(sorted({step for job in jobs for step in job.provenance.extraction})),
        normalization=tuple(
            sorted({step for job in jobs for step in job.provenance.normalization})
        ),
        merge=tuple(sorted({step for job in jobs for step in job.provenance.merge})),
    )

    return canonical.model_copy(
        update={
            "source_url": source_url,
            "fetched_at": fetched_at,
            "posted_at": posted_at,
            "title": title,
            "title_normalized": title,
            "description": description,
            "description_raw": canonical.description_raw or description,
            "description_clean": description,
            "company": company,
            "company_canonical": company_canonical,
            "company_name_raw": canonical.company_name_raw or company,
            "company_name_normalized": company_canonical or company,
            "canonical_url": canonical_url,
            "location": location,
            "work_mode": work_mode,
            "compensation": compensation,
            "extraction_status": extraction_status,
            "quality_score": quality_score,
            "relevance_score": relevance_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "review_reasons": review_reasons,
            "provenance": provenance,
            "metadata": metadata,
        }
    )


def job_seen_at(job: JobRecord) -> datetime:
    if job.fetched_at is not None:
        return job.fetched_at
    now = job.metadata.get("fetched_at")
    if isinstance(now, str):
        return datetime.fromisoformat(now)
    if isinstance(now, datetime):
        return now
    from datetime import UTC

    return datetime.now(UTC)


def compute_blocking_key(job: JobRecord) -> str:
    """Builds a coarse blocking key: normalized company + first word of normalized title."""
    company = (job.company_canonical or job.company or "").lower().strip()
    title_first = (job.title_normalized or job.title or "").lower().split()[:1]
    return sha256(f"{company}|{' '.join(title_first)}".encode()).hexdigest()[:16]


def create_job_group(job: JobRecord) -> JobGroup:
    group_id = compute_group_id(job)
    now = job_seen_at(job)
    blocking_key = compute_blocking_key(job)

    attribution = SourceAttribution(
        source_kind=job.source_kind,
        source_name=job.source_name,
        url=job.canonical_url,
        first_seen_at=now,
        last_seen_at=now,
    )

    return JobGroup(
        group_id=group_id,
        canonical_job=job,
        jobs=[job],
        source_attributions=[attribution],
        source_count=1,
        first_seen_at=now,
        last_seen_at=now,
        blocking_key=blocking_key,
        merge_confidence=1.0,
    )


def merge_job_into_group(
    group: JobGroup, job: JobRecord, *, merge_confidence: float = 1.0
) -> JobGroup:
    # Check if this source is already in the group
    existing_job_idx = -1
    for i, existing_job in enumerate(group.jobs):
        if (
            existing_job.source_kind == job.source_kind
            and existing_job.source_name == job.source_name
        ):
            existing_job_idx = i
            break

    new_jobs = list(group.jobs)
    if existing_job_idx >= 0:
        new_jobs[existing_job_idx] = job
    else:
        new_jobs.append(job)

    canonical_job = merge_jobs(new_jobs)
    now = job_seen_at(job)

    new_attributions = list(group.source_attributions)
    attr_idx = -1
    for i, attr in enumerate(new_attributions):
        if attr.source_kind == job.source_kind and attr.source_name == job.source_name:
            attr_idx = i
            break

    if attr_idx >= 0:
        old_attr = new_attributions[attr_idx]
        new_attributions[attr_idx] = SourceAttribution(
            source_kind=old_attr.source_kind,
            source_name=old_attr.source_name,
            url=job.canonical_url or old_attr.url,
            first_seen_at=old_attr.first_seen_at,
            last_seen_at=now,
        )
    else:
        new_attributions.append(
            SourceAttribution(
                source_kind=job.source_kind,
                source_name=job.source_name,
                url=job.canonical_url,
                first_seen_at=now,
                last_seen_at=now,
            )
        )

    # Use the minimum confidence seen across all merges in the group (most conservative)
    final_confidence = min(group.merge_confidence, merge_confidence)

    return JobGroup(
        group_id=group.group_id,
        canonical_job=canonical_job,
        jobs=new_jobs,
        source_attributions=new_attributions,
        source_count=len(new_jobs),
        first_seen_at=group.first_seen_at,
        last_seen_at=now,
        blocking_key=group.blocking_key or compute_blocking_key(canonical_job),
        merge_confidence=final_confidence,
        lifecycle_status=group.lifecycle_status,
    )


def remove_job_from_group(group: JobGroup, job_id: str) -> JobGroup | None:
    new_jobs = [j for j in group.jobs if j.stable_id != job_id]
    if not new_jobs:
        return None

    canonical_job = merge_jobs(new_jobs)

    # We must also keep source_attributions strictly matching the current jobs,
    # or just rebuild them. Rebuilding them is safer:
    kept_kinds_names = {(j.source_kind, j.source_name) for j in new_jobs}
    new_attributions = [
        attr
        for attr in group.source_attributions
        if (attr.source_kind, attr.source_name) in kept_kinds_names
    ]

    return JobGroup(
        group_id=group.group_id,
        canonical_job=canonical_job,
        jobs=new_jobs,
        source_attributions=new_attributions,
        source_count=len(new_jobs),
        first_seen_at=group.first_seen_at,
        last_seen_at=group.last_seen_at,
    )
