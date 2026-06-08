"""Utility for building text representation of a job for embedding/search."""

from __future__ import annotations

from job_ftch.domain import Job, WorkMode


def build_job_embedding_text(job: Job) -> str:
    """
    Build embedding text from normalized job.
    Uses title, company_canonical or company, location, work_mode, description.
    Skips empty parts.
    """
    parts = []

    if job.title:
        parts.append(job.title)

    company = job.company_canonical or job.company
    if company:
        parts.append(company)

    if job.location:
        parts.append(job.location)

    if job.work_mode != WorkMode.UNKNOWN:
        parts.append(str(job.work_mode))

    if job.description:
        parts.append(job.description)

    return "\n\n".join(parts).strip()
