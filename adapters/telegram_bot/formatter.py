"""Telegram HTML formatter helpers."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain import Job, JobRecord

_MAX_TITLE = 200
_MAX_COMPANY = 100
_MAX_LOCATION = 100
_MAX_URL = 500
_MAX_DESC = 300
_MAX_TOTAL = 3800


def _url_label(url: str) -> str:
    if "t.me" in url or "telegram.me" in url:
        return "Telegram"
    return "Source"


def format_job_message(job: JobRecord) -> str:
    title = escape(job.title or "Untitled role")[:_MAX_TITLE]
    company = escape(job.company or "Unknown company")[:_MAX_COMPANY]
    location = escape(job.location or "")[:_MAX_LOCATION]
    desc = escape((job.description or "")[:_MAX_DESC].strip())
    url = str(job.canonical_url)[:_MAX_URL] if job.canonical_url else None
    work_mode = escape(str(job.work_mode)) if job.work_mode else ""

    header = f"<b>{title}</b> — {company}"
    meta = " • ".join(filter(None, [location, work_mode]))
    url_line = f'<a href="{url}">{_url_label(url)}</a>' if url else ""

    parts = [header]
    if meta:
        parts.append(meta)
    if desc:
        parts.append("")
        parts.append(desc)
    if url_line:
        parts.append("")
        parts.append(url_line)

    return "\n".join(parts)[:_MAX_TOTAL]


def format_job_digest(jobs: Sequence[Job], *, page: int = 0, page_size: int = 1) -> str:
    start = page * page_size
    chunk = jobs[start : start + page_size]
    if not chunk:
        return "No jobs available."

    job = chunk[0]
    title = escape(job.title or "Untitled role")[:_MAX_TITLE]
    company = escape(job.company or "Unknown company")[:_MAX_COMPANY]
    location = escape(job.location or "")[:_MAX_LOCATION]
    desc = escape((job.description or "")[:_MAX_DESC].strip())
    url = str(job.canonical_url)[:_MAX_URL] if job.canonical_url else None
    work_mode = escape(str(job.work_mode)) if job.work_mode else ""

    header = f"<b>{title}</b> — {company}"
    meta = " • ".join(filter(None, [location, work_mode]))
    url_line = f'<a href="{url}">{_url_label(url)}</a>' if url else ""

    parts = [header]
    if meta:
        parts.append(meta)
    if desc:
        parts.append("")
        parts.append(desc)
    if url_line:
        parts.append("")
        parts.append(url_line)

    return "\n".join(parts)[:_MAX_TOTAL]
