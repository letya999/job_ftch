"""In-memory implementation of JobGroupStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

from application.registry import register_job_group_store
from domain import (
    JobGroup,
    compute_identity_fingerprint,
    create_job_group,
    merge_job_into_group,
)

if TYPE_CHECKING:
    from config import Settings
    from domain import Job


@register_job_group_store("memory")
class InMemoryJobGroupStore:
    """
    In-memory JobGroupStore with O(1) lookups by URL and fingerprint.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        del settings
        self._groups: dict[str, JobGroup] = {}
        self._url_index: dict[str, str] = {}  # canonical_url -> group_id
        self._fingerprint_index: dict[str, str] = {}  # fingerprint -> group_id

        # Stats tracking for RunSummary
        self.new_groups_created = 0
        self.merged_into_group = 0
        self.by_source_kind_new: dict[str, int] = {}
        self.by_source_kind_merged: dict[str, int] = {}

    async def get_group(self, group_id: str) -> JobGroup | None:
        return self._groups.get(group_id)

    async def create(self, job: Job) -> JobGroup:
        group = create_job_group(job)
        group_id = group.group_id
        fingerprint = compute_identity_fingerprint(job)

        self._groups[group_id] = group
        self._fingerprint_index[fingerprint] = group_id
        if job.canonical_url:
            self._url_index[str(job.canonical_url)] = group_id

        self.new_groups_created += 1
        sk = str(job.source_kind)
        self.by_source_kind_new[sk] = self.by_source_kind_new.get(sk, 0) + 1

        return group

    async def merge(self, group_id: str, job: Job) -> JobGroup:
        group = self._groups.get(group_id)
        if not group:
            raise ValueError(f"Group {group_id} not found.")

        updated_group = merge_job_into_group(group, job)

        self._groups[group_id] = updated_group

        # Update indices if canonical job changed
        if updated_group.canonical_job.canonical_url:
            self._url_index[str(updated_group.canonical_job.canonical_url)] = group_id

        self.merged_into_group += 1
        sk = str(job.source_kind)
        self.by_source_kind_merged[sk] = self.by_source_kind_merged.get(sk, 0) + 1

        return updated_group

    async def find_by_url(self, canonical_url: str) -> JobGroup | None:
        group_id = self._url_index.get(canonical_url)
        if group_id:
            return self._groups.get(group_id)
        return None

    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None:
        group_id = self._fingerprint_index.get(fingerprint)
        if group_id:
            return self._groups.get(group_id)
        return None

    async def list_groups(self, limit: int = 100) -> list[JobGroup]:
        return list(self._groups.values())[:limit]

    async def count(self) -> int:
        return len(self._groups)
