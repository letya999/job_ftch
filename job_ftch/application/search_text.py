"""Utility for building text representation of a job for embedding/search."""

from __future__ import annotations

from job_ftch.domain import JobRecord, WorkMode


def build_job_embedding_text(job: JobRecord) -> str:
    """
    Build embedding text from normalized job.
    Uses structured role, company, location, skills, and description fields.
    Skips empty parts.
    """
    parts = []

    if job.title_normalized or job.title:
        parts.append(job.title_normalized or job.title or "")

    if job.role_family:
        parts.append(job.role_family)

    if job.domain:
        parts.append(job.domain)

    company = job.company_canonical or job.company
    if company:
        parts.append(company)

    location = job.location or job.city or job.region or job.country
    if location:
        parts.append(location)

    if job.work_mode != WorkMode.UNKNOWN:
        parts.append(str(job.work_mode))

    if job.skills_explicit:
        parts.append(", ".join(skill.canonical_name for skill in job.skills_explicit))

    if job.tools_stack:
        parts.append(", ".join(job.tools_stack))

    if job.description:
        parts.append(job.description)

    return "\n\n".join(parts).strip()
