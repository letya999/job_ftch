"""Round-robin interleaving for publication queues.

Mixes jobs from different sources/companies/role_families so consecutive
posts feel diverse without imposing hard limits or dropping anything.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import cycle
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.domain import Job


def _bucket_key(job: Job) -> str:
    parts: list[str] = []
    company = getattr(job, "company_canonical", None) or getattr(job, "company", None)
    if company:
        parts.append(company.strip().lower())
    role_family = getattr(job, "role_family", None)
    if role_family:
        parts.append(role_family.strip().lower())
    source = getattr(job, "source_name", None)
    if source:
        parts.append(source.strip().lower())
    return "|".join(parts) if parts else "unknown"


def interleave_jobs(jobs: Sequence[Job]) -> list[Job]:
    """Round-robin interleave by company+role_family+source.

    No jobs are dropped; output length == input length.
    """
    if len(jobs) <= 1:
        return list(jobs)

    buckets: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        buckets[_bucket_key(job)].append(job)

    result: list[Job] = []
    seen: set[int] = set()
    iterators = {key: iter(items) for key, items in buckets.items()}
    key_cycle = cycle(list(iterators.keys()))

    while len(result) < len(jobs):
        key = next(key_cycle)
        it = iterators.get(key)
        if it is None:
            continue
        try:
            job = next(it)
            job_id_hash = id(job)
            if job_id_hash not in seen:
                seen.add(job_id_hash)
                result.append(job)
        except StopIteration:
            del iterators[key]
            if not iterators:
                break
            key_cycle = cycle(list(iterators.keys()))

    return result
