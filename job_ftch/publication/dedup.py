"""Fingerprint-based publication dedup.

Catches near-duplicate vacancies that pass id-based ledger dedup:
same company+role+salary from different aggregators or URLs.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain import Job


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    t = text.strip().lower()
    t = unicodedata.normalize("NFKC", t)
    return " ".join(t.split())


def _fingerprint(job: Job) -> str:
    company = _normalize_text(
        getattr(job, "company_canonical", None) or getattr(job, "company", None)
    )
    role = _normalize_text(job.title)

    comp = getattr(job, "compensation", None)
    salary = ""
    if comp is not None:
        lo = getattr(comp, "min_amount", None) or ""
        hi = getattr(comp, "max_amount", None) or ""
        cur = getattr(comp, "currency", "") or ""
        salary = f"{lo}|{hi}|{cur}"

    return f"{company}::{role}::{salary}"


def deduplicate_for_publish(jobs: Sequence[Job]) -> list[Job]:
    """Remove fingerprint-duplicates, keeping the first occurrence.

    Priority: direct source (career_site) over aggregator, then earlier in list.
    """
    seen: dict[str, int] = {}
    result: list[Job] = list(jobs)

    for idx, job in enumerate(result):
        fp = _fingerprint(job)
        if not fp or fp == "::::":
            continue
        if fp in seen:
            prev_idx = seen[fp]
            prev_kind = getattr(result[prev_idx], "source_kind", None)
            curr_kind = getattr(job, "source_kind", None)
            prev_val = prev_kind.value if prev_kind and hasattr(prev_kind, "value") else ""
            curr_val = curr_kind.value if curr_kind and hasattr(curr_kind, "value") else ""
            if prev_val != "career_site" and curr_val == "career_site":
                seen[fp] = idx
        else:
            seen[fp] = idx

    keep_indices = set(seen.values())
    return [
        job for idx, job in enumerate(result) if idx in keep_indices or _fingerprint(job) == "::::"
    ]
