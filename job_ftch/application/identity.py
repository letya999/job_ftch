"""Logic for cross-source job identity matching."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.contracts import JobGroupStore
    from job_ftch.domain import JobRecord


class JobIdentityMatcher:
    """
    Two-step matching ladder on Job objects (post-extraction).
    Step 1: exact canonical_url match — O(1) via url index in JobGroupStore
    Step 2: fingerprint match — sha256(company_canonical + normalized_title + location)
    """

    def __init__(self, store: JobGroupStore) -> None:
        self._store = store

    async def find_matching_group(self, job: JobRecord) -> str | None:
        """Return group_id of matching JobGroup, or None if no match."""
        # Step 1: exact URL match
        if job.canonical_url:
            group = await self._store.find_by_url(str(job.canonical_url))
            if group:
                return group.group_id

        # Step 2: fingerprint match
        from job_ftch.domain import compute_identity_fingerprint

        fingerprint = compute_identity_fingerprint(job)
        group = await self._store.find_by_fingerprint(fingerprint)
        if group:
            return group.group_id

        return None
