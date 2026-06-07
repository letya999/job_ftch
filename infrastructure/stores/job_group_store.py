"""In-memory implementation of JobGroupStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

from application.registry import register_job_group_store
from domain import (
    JobGroup,
    SourceAttribution,
    compute_group_id,
    compute_identity_fingerprint,
    merge_jobs,
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

    async def get(self, group_id: str) -> JobGroup | None:
        return self._groups.get(group_id)

    async def create(self, job: Job) -> JobGroup:
        group_id = compute_group_id(job)
        fingerprint = compute_identity_fingerprint(job)

        now = job.metadata.get("fetched_at")
        if isinstance(now, str):
            from datetime import datetime

            now = datetime.fromisoformat(now)
        else:
            from datetime import UTC, datetime

            now = datetime.now(UTC)

        attribution = SourceAttribution(
            source_kind=job.source_kind,
            source_name=job.source_name,
            url=job.canonical_url,
            first_seen_at=now,
            last_seen_at=now,
        )

        group = JobGroup(
            group_id=group_id,
            canonical_job=job,
            jobs=[job],
            source_attributions=[attribution],
            source_count=1,
            first_seen_at=now,
            last_seen_at=now,
        )

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

        # Check if this source is already in the group
        existing_job_idx = -1
        for i, existing_job in enumerate(group.jobs):
            if (
                existing_job.source_kind == job.source_kind
                and existing_job.source_name == job.source_name
            ):
                existing_job_idx = i
                break

        new_jobs = list(group.jobs)
        if existing_job_idx >= 0:
            # Update existing job from this source
            new_jobs[existing_job_idx] = job
        else:
            # Add new source to group
            new_jobs.append(job)

        # Re-merge to get canonical record
        canonical_job = merge_jobs(new_jobs)

        # Update attributions
        now = job.metadata.get("fetched_at")
        if isinstance(now, str):
            from datetime import datetime

            now = datetime.fromisoformat(now)
        else:
            from datetime import UTC, datetime

            now = datetime.now(UTC)

        new_attributions = list(group.source_attributions)
        attr_idx = -1
        for i, attr in enumerate(new_attributions):
            if attr.source_kind == job.source_kind and attr.source_name == job.source_name:
                attr_idx = i
                break

        if attr_idx >= 0:
            old_attr = new_attributions[attr_idx]
            new_attributions[attr_idx] = SourceAttribution(
                source_kind=old_attr.source_kind,
                source_name=old_attr.source_name,
                url=job.canonical_url or old_attr.url,
                first_seen_at=old_attr.first_seen_at,
                last_seen_at=now,
            )
        else:
            new_attributions.append(
                SourceAttribution(
                    source_kind=job.source_kind,
                    source_name=job.source_name,
                    url=job.canonical_url,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

        updated_group = JobGroup(
            group_id=group.group_id,
            canonical_job=canonical_job,
            jobs=new_jobs,
            source_attributions=new_attributions,
            source_count=len(new_jobs),
            first_seen_at=group.first_seen_at,
            last_seen_at=now,
        )

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
