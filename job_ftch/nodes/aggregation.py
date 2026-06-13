"""Cross-source job aggregation node."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from job_ftch.application.identity import JobIdentityMatcher

if TYPE_CHECKING:
    from job_ftch.application.contracts import JobGroupStore
    from job_ftch.domain import JobRecord


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
        group_id, merge_hint = await self._find_or_create_group(job)

        if not self._attach_group_id:
            return job

        return job.model_copy(
            update={
                "group_id": group_id,
                "provenance": job.provenance.model_copy(
                    update={"merge": tuple(list(job.provenance.merge) + [merge_hint])}
                ),
                "metadata": {
                    **job.metadata,
                    "group_id": group_id,
                },
            }
        )

    async def _find_or_create_group(self, job: JobRecord) -> tuple[str, str]:
        # Step 1: exact URL match
        if job.canonical_url:
            group = await self._store.find_by_url(str(job.canonical_url))
            if group:
                await self._store.merge(group.group_id, job, merge_confidence=1.0)
                return group.group_id, "merge:exact_url_match"

        # Step 2: identity fingerprint match
        group_id = await self._matcher.find_matching_group(job)
        if group_id:
            await self._store.merge(group_id, job, merge_confidence=0.95)
            return group_id, "merge:exact_identity_match"

        # Step 3: fuzzy title match via blocking key
        if self._enable_fuzzy:
            from job_ftch.domain.job_group import compute_blocking_key
            
            blocking_key = compute_blocking_key(job)
            groups = await self._store.find_by_blocking_key(blocking_key, limit=50)
            
            for group in groups:
                score = self._compute_fuzzy_score(job, group.canonical_job)
                if score >= self._fuzzy_title_threshold:
                    confidence = score / 100.0 * 0.85
                    await self._store.merge(group.group_id, job, merge_confidence=confidence)
                    return group.group_id, "merge:fuzzy_title_company_match"

        # On no match: store.create(job) -> new JobGroup
        new_group = await self._store.create(job)
        return new_group.group_id, "merge:new_group_created"

    def _compute_fuzzy_score(self, job: JobRecord, other: JobRecord) -> float:
        # Same company (canonical) + fuzzy title
        job_company = job.company_canonical or job.company
        other_company = other.company_canonical or other.company

        if not job_company or not other_company or job_company.lower() != other_company.lower():
            return 0.0

        return float(fuzz.ratio(job.title or "", other.title or ""))

    def _is_fuzzy_match(self, job: JobRecord, other: JobRecord) -> bool:
        """Deprecated: use _compute_fuzzy_score instead."""
        return self._compute_fuzzy_score(job, other) >= self._fuzzy_title_threshold
