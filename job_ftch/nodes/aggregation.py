"""Cross-source job aggregation node."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from job_ftch.application.identity import JobIdentityMatcher

if TYPE_CHECKING:
    from job_ftch.application.contracts import JobGroupStore
    from job_ftch.domain import Job, JobRecord


class JobAggregationNode:
    """
    Stage[JobRecord, JobRecord] — cross-source aggregation.
    JobRecord passes through with canonical group attachment.
    Side-effect: creates or updates a JobGroup in JobGroupStore.
    Placed AFTER ExtractionNode (and optionally CompanyCanonicalizer from Phase 13).
    """

    def __init__(
        self,
        store: JobGroupStore,
        fuzzy_title_threshold: float = 85.0,
        enable_fuzzy: bool = True,
        attach_group_id: bool = False,
    ) -> None:
        self._store = store
        self._matcher = JobIdentityMatcher(store)
        self._fuzzy_title_threshold = fuzzy_title_threshold
        self._enable_fuzzy = enable_fuzzy
        self._attach_group_id = attach_group_id

    async def process(self, job: JobRecord) -> JobRecord:
        group_id = await self._find_or_create_group(job)

        if not self._attach_group_id:
            return job

        return job.model_copy(
            update={
                "group_id": group_id,
                "metadata": {
                    **job.metadata,
                    "group_id": group_id,
                }
            }
        )

    async def _find_or_create_group(self, job: JobRecord) -> str:
        # Step 1 & 2: exact match via matcher (URL or Fingerprint)
        group_id = await self._matcher.find_matching_group(job)
        if group_id:
            await self._store.merge(group_id, job)
            return group_id

        # Step 3: fuzzy title match via rapidfuzz against candidates
        if self._enable_fuzzy:
            # Simple candidate fetch: list recent groups
            # In production, this would be a more targeted query
            groups = await self._store.list_groups(limit=1000)
            for group in groups:
                if self._is_fuzzy_match(job, group.canonical_job):
                    await self._store.merge(group.group_id, job)
                    return group.group_id

        # On no match: store.create(job) -> new JobGroup
        new_group = await self._store.create(job)
        return new_group.group_id

    def _is_fuzzy_match(self, job: JobRecord, other: Job) -> bool:
        # Same company (canonical) + fuzzy title
        job_company = job.company_canonical or job.company
        other_company = other.company_canonical or other.company

        if not job_company or not other_company or job_company.lower() != other_company.lower():
            return False

        score = fuzz.ratio(job.title or "", other.title or "")
        return bool(score >= self._fuzzy_title_threshold)
