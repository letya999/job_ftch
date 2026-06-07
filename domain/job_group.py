"""Domain models for cross-source job aggregation."""

from __future__ import annotations

import string
from datetime import datetime  # noqa: TC003
from hashlib import sha256
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict

from .models import Job, JobExtractionStatus, SourceKind, WorkMode


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
    canonical_job: Job  # merged best-field record
    jobs: list[Job]  # one per unique source (ordered by source priority)
    source_attributions: list[SourceAttribution]
    source_count: int  # = len(jobs)
    first_seen_at: datetime
    last_seen_at: datetime


def compute_group_id(job: Job) -> str:
    """
    Computes a unique group_id for a job.
    If canonical_url is present, it's used as the primary identifier.
    Otherwise, uses the semantic identity fingerprint.
    """
    if job.canonical_url:
        return sha256(str(job.canonical_url).encode("utf-8")).hexdigest()
    return compute_identity_fingerprint(job)


def compute_identity_fingerprint(job: Job) -> str:
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


def merge_jobs(jobs: list[Job]) -> Job:
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

    def get_priority(j: Job) -> int:
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

    # quality_score, relevance_score: max of all
    quality_scores = [j.quality_score for j in jobs if j.quality_score is not None]
    quality_score = max(quality_scores) if quality_scores else None

    relevance_scores = [j.relevance_score for j in jobs if j.relevance_score is not None]
    relevance_score = max(relevance_scores) if relevance_scores else None

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

    return Job(
        raw_item_id=canonical.raw_item_id,
        source_kind=canonical.source_kind,
        source_name=canonical.source_name,
        title=title,
        company=company,
        company_canonical=company_canonical,
        description=description,
        canonical_url=canonical_url,
        location=location,
        work_mode=work_mode,
        compensation=compensation,
        extraction_status=extraction_status,
        quality_score=quality_score,
        relevance_score=relevance_score,
        review_reasons=review_reasons,
        metadata=metadata,
    )
