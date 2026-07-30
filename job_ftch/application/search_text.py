"""Utility for building text representation of a job for embedding/search.

Structured-first composition: high-signal fields (title, role, skills) are
placed first so they dominate the embedding vector.  Description is truncated
to avoid drowning out structural information within the 512-token limit of
models like multilingual-e5-small.
"""

from __future__ import annotations

from job_ftch.domain import JobRecord, WorkMode

# ~75 tokens — leaves room for structure within 512-token models.
_MAX_DESC_CHARS = 300


def build_job_embedding_text(job: JobRecord) -> str:
    """Build embedding text with structured-first composition.

    Priority order:
    1. Title + role family + company  (highest signal density)
    2. Domain + location + work mode  (context)
    3. Skills + tools                 (discriminative features)
    4. Description (truncated)        (supplementary)
    """
    parts: list[str] = []

    # 1. High-signal structured header
    header_parts: list[str] = []
    title = job.title_normalized or job.title
    if title:
        header_parts.append(title)
    if job.role_family and job.role_family != title:
        header_parts.append(f"({job.role_family})")
    company = job.company_canonical or job.company
    if company:
        header_parts.append(f"at {company}")
    if header_parts:
        parts.append(" ".join(header_parts))

    # 2. Domain + location context
    context: list[str] = []
    if job.domain:
        context.append(job.domain)
    location = job.location or job.city or job.region or job.country
    if location:
        context.append(location)
    if job.work_mode != WorkMode.UNKNOWN:
        context.append(str(job.work_mode))
    if context:
        parts.append(", ".join(context))

    # 3. Skills (high signal density, compact)
    if job.skills_explicit:
        skills = ", ".join(skill.canonical_name for skill in job.skills_explicit[:20])
        parts.append(f"Skills: {skills}")

    if job.tools_stack:
        parts.append(f"Stack: {', '.join(job.tools_stack[:15])}")

    # 4. Description (truncated to avoid drowning structural fields)
    if job.description:
        desc = job.description[:_MAX_DESC_CHARS]
        # Truncate at last word boundary to avoid mid-word cuts
        if len(job.description) > _MAX_DESC_CHARS:
            last_space = desc.rfind(" ")
            if last_space > _MAX_DESC_CHARS // 2:
                desc = desc[:last_space]
        parts.append(desc)

    return "\n\n".join(parts).strip()
