"""Telegram HTML formatter helpers."""

from __future__ import annotations

from html import escape

from job_ftch.domain import Job


def format_job_message(job: Job) -> str:
    title = escape(job.title or "Untitled role")
    company = escape(job.company or "Unknown company")
    location = escape(job.location or "Unknown location")
    description = escape(job.description[:280].strip())
    url = escape(str(job.canonical_url)) if job.canonical_url else "No URL"
    work_mode = escape(str(job.work_mode))
    return f"<b>{title}</b>\n{company}\n{location} • {work_mode}\n\n{description}\n\n{url}"


def format_job_digest(jobs: list[Job], *, page: int = 0, page_size: int = 5) -> str:
    start = page * page_size
    chunk = jobs[start : start + page_size]
    if not chunk:
        return "No jobs available."
    lines = []
    for index, job in enumerate(chunk, start=1 + start):
        title = escape(job.title or "Untitled role")
        company = escape(job.company or "Unknown company")
        lines.append(f"{index}. <b>{title}</b> — {company}")
    return "\n".join(lines)
