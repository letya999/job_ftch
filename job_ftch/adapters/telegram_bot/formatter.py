"""Telegram HTML formatter helpers."""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING

from job_ftch.publication.normalize import format_compensation, format_geo

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain import Job, JobRecord

_MAX_TITLE = 200
_MAX_COMPANY = 100
_MAX_LOCATION = 100
_MAX_URL = 500
_MAX_DESC = 300
_MAX_TOTAL = 3800


def _safe_truncate_html(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    truncated = re.sub(r"&[^;]{0,10}$", "", truncated)
    truncated = re.sub(r"<[^>]{0,100}$", "", truncated)
    return truncated.rstrip() + "…"


def _is_fake_company(company: str | None, source_name: str | None) -> bool:
    """Returns True if company field is just the source name or a technical identifier."""
    if not company:
        return True
    c = company.lower()
    # Matches source_name exactly
    if source_name and c == source_name.lower():
        return True
    # Technical patterns: contains underscore+tld, looks like domain slug
    if any(pat in c for pat in ["_com", "_ru", "_kz", "_org", "hh.ru", "habr.com"]):
        return True
    # Common placeholder values used by extractors
    if c in ("unknown", "недоступно", "—", "-", "n/a", ""):
        return True

    # HTML fragments or sentence-like prose are not company names
    if "<" in c or ">" in c:
        return True
    return bool(len(company) > 50 or c.count(" ") >= 6)


_SAFE_SCHEMES = ("https://", "http://", "t.me/", "telegram.me/", "tg://")


def _safe_href(url: str | None) -> str | None:
    """Return url only if it has a known-safe scheme; None otherwise."""
    if not url:
        return None
    url_str = str(url).strip()
    if any(url_str.startswith(scheme) for scheme in _SAFE_SCHEMES):
        # Escape only the chars that break the HTML attribute: & " < >
        return (
            url_str.replace("&", "&amp;")
            .replace('"', "%22")
            .replace("<", "%3C")
            .replace(">", "%3E")
        )
    return None


def resolve_job_url(job: Job | JobRecord) -> str | None:
    """Preferred outward-facing URL for cards and publish-time checks."""
    url_raw = job.canonical_url
    if not url_raw and hasattr(job, "urls") and job.urls:
        url_raw = job.urls[0]
    return str(url_raw) if url_raw else None


def format_vacancy_card(job: Job | JobRecord) -> str:
    title = escape(job.title or "Без названия")[:_MAX_TITLE]

    source_name = getattr(job, "source_name", None)
    company_raw = job.company
    company = (
        "—"
        if _is_fake_company(company_raw, source_name)
        else escape(company_raw or "—")[:_MAX_COMPANY]
    )

    wm = str(job.work_mode).lower() if job.work_mode else ""
    if any(x in wm for x in ["remote", "дистанционно", "удаленка", "remotely"]):
        work_mode = "🌍 Remote"
    elif any(x in wm for x in ["office", "офис"]):
        work_mode = "🏢 Office"
    elif any(x in wm for x in ["hybrid", "гибрид"]):
        work_mode = "🔀 Hybrid"
    else:
        work_mode = None

    # Location
    normalized_location = format_geo(job)
    location = escape(normalized_location or "")[:_MAX_LOCATION] if normalized_location else None

    # Salary from nested compensation object
    salary_part = ""
    salary = format_compensation(job)
    if salary:
        salary_part = f" • 💰 {salary}"

    safe_url = _safe_href(resolve_job_url(job))
    url_line = f'🔗 <a href="{safe_url}">Открыть вакансию</a>' if safe_url else ""

    desc = escape((job.description or "")[:_MAX_DESC].strip())
    desc_block = f"\n<i>{desc}...</i>" if desc else ""

    # Build meta line: company, work_mode/location, salary
    meta_parts = []
    if company != "—":
        meta_parts.append(f"🏢 {company}")
    geo = location or (work_mode if work_mode else None)
    if work_mode and location:
        geo = f"{location} • {work_mode}"
    if geo:
        meta_parts.append(geo)
    if salary_part:
        meta_parts.append(salary_part.removeprefix(" • "))
    meta_line = " • ".join(meta_parts) if meta_parts else ""

    parts = [f"🔵 <b>{title}</b>"]
    if meta_line:
        parts.append(meta_line)
    if desc_block:
        parts.append(desc_block)
    if url_line:
        parts.append(url_line)

    return _safe_truncate_html("\n".join(parts), _MAX_TOTAL)


def format_job_message(job: JobRecord) -> str:
    # Legacy wrapper for format_vacancy_card style
    return format_vacancy_card(job)


def format_job_digest(jobs: Sequence[Job], *, page: int = 0, page_size: int = 1) -> str:
    # For now, just format the first job in chunk
    start = page * page_size
    chunk = jobs[start : start + page_size]
    if not chunk:
        return "No jobs available."
    return format_vacancy_card(chunk[0])
