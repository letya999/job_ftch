"""Fingerprint-based publication dedup.

Catches near-duplicate vacancies that pass id-based ledger dedup:
same company+role+salary from different aggregators or URLs.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain import Job


def _normalize_text(text: str | None) -> str:
    if not isinstance(text, str) or not text:
        return ""
    t = text.strip().lower()
    t = unicodedata.normalize("NFKC", t)
    return " ".join(t.split())


def same_vacancy(first: Job, second: Job) -> bool:
    """Require matching role and content; metadata alone can describe two openings."""
    title_a = _normalize_text(getattr(first, "title", None))
    title_b = _normalize_text(getattr(second, "title", None))
    if not title_a or not title_b or fuzz.ratio(title_a, title_b) < 90:
        return False

    company_a = _normalize_text(
        getattr(first, "company_canonical", None) or getattr(first, "company", None)
    )
    company_b = _normalize_text(
        getattr(second, "company_canonical", None) or getattr(second, "company", None)
    )
    if (
        company_a
        and company_b
        and company_a != company_b
        and (
            min(len(company_a), len(company_b)) < 4
            or not (company_a.startswith(company_b) or company_b.startswith(company_a))
        )
    ):
        return False

    location_a = _normalize_text(getattr(first, "location", None))
    location_b = _normalize_text(getattr(second, "location", None))
    if (
        location_a
        and location_b
        and not (
            location_a == location_b
            or location_a.startswith(location_b + ",")
            or location_b.startswith(location_a + ",")
        )
    ):
        return False

    salary_a = getattr(first, "compensation", None)
    salary_b = getattr(second, "compensation", None)
    if salary_a and salary_b and salary_a != salary_b:
        return False

    desc_a = _normalize_text(getattr(first, "description", None))
    desc_b = _normalize_text(getattr(second, "description", None))
    if len(desc_a) >= 120 and len(desc_b) >= 120:
        # shortcut: missing company labels require almost identical source text;
        # upgrade when a reliable cross-source company identity is available.
        threshold = 88 if company_a and company_b else 98
        return (
            fuzz.ratio(desc_a, desc_b) >= threshold and fuzz.token_set_ratio(desc_a, desc_b) >= 92
        )

    return bool(company_a and company_a == company_b and title_a == title_b and desc_a == desc_b)


def deduplicate_for_publish(jobs: Sequence[Job]) -> list[Job]:
    """Keep one per confidently matching description, preferring direct sources."""
    result: list[Job] = []
    for job in jobs:
        match = next((idx for idx, prior in enumerate(result) if same_vacancy(job, prior)), None)
        if match is None:
            result.append(job)
            continue
        prior = result[match]
        prior_kind = getattr(getattr(prior, "source_kind", None), "value", "")
        current_kind = getattr(getattr(job, "source_kind", None), "value", "")
        if prior_kind != "career_site" and current_kind == "career_site":
            result[match] = job
    return result
