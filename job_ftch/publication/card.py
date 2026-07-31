"""PublicationCard - the normalised, sink-agnostic vacancy card.

Built deterministically from JobRecord structured fields.
LLM involvement is limited to summary/key_requirements (upstream).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from job_ftch.domain import Job, JobRecord

from job_ftch.publication.normalize import (
    detect_content_language,
    format_compensation,
    format_location,
    pick_source_label,
    resolve_card_url,
    summarise_requirements,
    summarise_stack,
)


class PublicationCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1, max_length=200)
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    summary: str | None = Field(default=None, max_length=300)
    key_requirements: str | None = Field(default=None, max_length=300)
    stack: str | None = Field(default=None, max_length=200)
    url: str | None = None
    source_name: str | None = None
    source_kind: str | None = None
    language: str = "en"
    job_id: str = ""


def build_card(job: Job | JobRecord) -> PublicationCard:
    """Deterministic card builder - no LLM, no I/O."""
    from job_ftch.adapters.telegram_bot.formatter import _is_fake_company

    title = (job.title or job.title_raw or "Job posting").strip()[:200]
    source_name = getattr(job, "source_name", None)

    company_raw = getattr(job, "company_canonical", None) or job.company
    company = (
        None
        if _is_fake_company(company_raw, source_name)
        else (company_raw or "").strip()[:100] or None
    )

    content_lang = detect_content_language(job)
    location = format_location(job, lang=content_lang)
    salary = format_compensation(job, lang=content_lang)
    url = resolve_card_url(job)

    presentable = getattr(job, "presentable", None)
    summary = None
    if presentable is not None:
        body = getattr(presentable, "body", None)
        if body and not body.startswith("#"):
            summary = body.strip()[:300] or None

    key_requirements = summarise_requirements(job)
    stack = summarise_stack(job)

    language = content_lang

    source_kind_attr = getattr(job, "source_kind", None)
    source_kind = (
        source_kind_attr.value if source_kind_attr and hasattr(source_kind_attr, "value") else None
    )

    job_id = (
        getattr(job, "job_id", None)
        or getattr(job, "stable_id", None)
        or getattr(job, "group_id", "")
        or ""
    )

    return PublicationCard(
        role=title,
        company=company,
        location=location,
        salary=salary,
        summary=summary,
        key_requirements=key_requirements,
        stack=stack,
        url=url,
        source_name=pick_source_label(job),
        source_kind=source_kind,
        language=language,
        job_id=job_id,
    )
