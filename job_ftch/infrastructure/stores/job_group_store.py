"""In-memory implementation of JobGroupStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.registry import register_job_group_store
from job_ftch.domain import (
    JobGroup,
    compute_identity_fingerprint,
    create_job_group,
    merge_job_into_group,
)

if TYPE_CHECKING:
    from job_ftch.config import Settings
    from job_ftch.domain import JobRecord


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
        self._blocking_index: dict[str, list[str]] = {}  # blocking_key -> [group_id]

        # Stats tracking for RunSummary
        self.new_groups_created = 0
        self.merged_into_group = 0
        self.by_source_kind_new: dict[str, int] = {}
        self.by_source_kind_merged: dict[str, int] = {}

    async def get_group(self, group_id: str) -> JobGroup | None:
        return self._groups.get(group_id)

    async def create(self, job: JobRecord) -> JobGroup:
        group = create_job_group(job)
        group_id = group.group_id
        fingerprint = compute_identity_fingerprint(job)

        self._groups[group_id] = group
        self._fingerprint_index[fingerprint] = group_id
        if job.canonical_url:
            self._url_index[str(job.canonical_url)] = group_id

        if group.blocking_key:
            if group.blocking_key not in self._blocking_index:
                self._blocking_index[group.blocking_key] = []
            self._blocking_index[group.blocking_key].append(group_id)

        self.new_groups_created += 1
        sk = str(job.source_kind)
        self.by_source_kind_new[sk] = self.by_source_kind_new.get(sk, 0) + 1

        return group

    async def merge(
        self, group_id: str, job: JobRecord, merge_confidence: float = 1.0
    ) -> JobGroup:
        group = self._groups.get(group_id)
        if not group:
            raise ValueError(f"Group {group_id} not found.")

        updated_group = merge_job_into_group(group, job, merge_confidence=merge_confidence)

        self._groups[group_id] = updated_group

        # Update indices if canonical job changed
        if updated_group.canonical_job.canonical_url:
            self._url_index[str(updated_group.canonical_job.canonical_url)] = group_id

        # Update blocking index if it changed (unlikely but possible if canonical changes)
        if updated_group.blocking_key and updated_group.blocking_key != group.blocking_key:
            if group.blocking_key in self._blocking_index:
                try:
                    self._blocking_index[group.blocking_key].remove(group_id)
                except ValueError:
                    pass
            if updated_group.blocking_key not in self._blocking_index:
                self._blocking_index[updated_group.blocking_key] = []
            self._blocking_index[updated_group.blocking_key].append(group_id)

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

    async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]:
        group_ids = self._blocking_index.get(key, [])
        return [self._groups[gid] for gid in group_ids[:limit] if gid in self._groups]

    async def list_groups(self, limit: int = 100) -> list[JobGroup]:
        return list(self._groups.values())[:limit]

    async def count(self) -> int:
        return len(self._groups)
