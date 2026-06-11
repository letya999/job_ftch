"""Telegram HTML formatter helpers."""

from __future__ import annotations

from html import escape

from job_ftch.domain import Job

_MAX_TITLE = 200
_MAX_COMPANY = 100
_MAX_LOCATION = 100
_MAX_URL = 500
_MAX_DESCRIPTION = 280
_MAX_TOTAL = 3800


def format_job_message(job: Job) -> str:
    title = escape(job.title or "Untitled role")[:_MAX_TITLE]
    company = escape(job.company or "Unknown company")[:_MAX_COMPANY]
    location = escape(job.location or "Unknown location")[:_MAX_LOCATION]
    description = escape((job.description or "")[:_MAX_DESCRIPTION].strip())
    url = str(job.canonical_url)[:_MAX_URL] if job.canonical_url else "No URL"
    work_mode = escape(str(job.work_mode))
    message = f"<b>{title}</b>\n{company}\n{location} • {work_mode}\n\n{description}\n\n{url}"
    return message[:_MAX_TOTAL]


def format_job_digest(jobs: list[Job], *, page: int = 0, page_size: int = 5) -> str:
    start = page * page_size
    chunk = jobs[start : start + page_size]
    if not chunk:
        return "No jobs available."
    lines = []
    for index, job in enumerate(chunk, start=1 + start):
        title = escape(job.title or "Untitled role")[:_MAX_TITLE]
        company = escape(job.company or "Unknown company")[:_MAX_COMPANY]
        lines.append(f"{index}. <b>{title}</b> — {company}")
    return "\n".join(lines)
