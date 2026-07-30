"""Lifecycle/status enrichment for explicit closed or filled vacancy signals."""

from __future__ import annotations

from job_ftch.domain import JobRecord, JobStatus, ProvenanceTrail

_CLOSED_MARKERS = (
    "position closed",
    "vacancy closed",
    "role closed",
    "hiring closed",
    "hiring complete",
    "role has been filled",
    "position has been filled",
    "роль закрыта",
    "вакансия закрыта",
    "позиция закрыта",
    "набор закрыт",
)

_FILLED_VALUES = {"filled", "closed", "expired", "inactive", "archived"}
_OPEN_VALUES = {"open", "active", "published", "live"}


class JobLifecycleNode:
    """Marks jobs as filled when the source payload explicitly says so."""

    async def process(self, item: JobRecord) -> JobRecord:
        status = self._infer_status(item)
        freshness_state = self._freshness_state(item, status)
        metadata = {**item.metadata, "freshness_evidence_state": freshness_state}
        if status in {None, item.status}:
            return item.model_copy(update={"metadata": metadata})
        return item.model_copy(
            update={
                "status": status,
                "metadata": metadata,
                "provenance": ProvenanceTrail(
                    extraction=item.provenance.extraction,
                    normalization=item.provenance.normalization
                    + ("lifecycle:explicit_status_signal",),
                    merge=item.provenance.merge,
                ),
            }
        )

    def _infer_status(self, item: JobRecord) -> JobStatus | None:
        metadata = item.metadata
        for key in ("status", "state", "job_status", "lifecycle_status"):
            value = metadata.get(key)
            normalized = self._normalize(value)
            if normalized in _OPEN_VALUES:
                return JobStatus.OPEN
            if normalized in _FILLED_VALUES:
                return JobStatus.FILLED

        for key in ("closed", "is_closed", "filled", "is_filled"):
            value = metadata.get(key)
            if value is True:
                return JobStatus.FILLED
            if value is False:
                return JobStatus.OPEN

        haystack = "\n".join(
            part
            for part in (item.title, item.description, item.title_raw, item.description_raw)
            if part
        ).casefold()
        if any(marker in haystack for marker in _CLOSED_MARKERS):
            return JobStatus.FILLED
        return None

    @staticmethod
    def _freshness_state(item: JobRecord, inferred: JobStatus | None) -> str:
        """Classify the evidence available to freshness policy, not relevance."""
        if inferred in {JobStatus.OPEN, JobStatus.FILLED}:
            return "explicit_status"
        if item.posted_at is not None or item.fetched_at is not None:
            return "observed_at"
        if item.canonical_url is not None or item.source_url is not None:
            return "locator_only"
        return "missing"

    @staticmethod
    def _normalize(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().casefold()
        return normalized or None
