"""Pre-ingest source assessment orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from job_ftch.domain.runtime_source import source_spec_identifier

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store
    from job_ftch.domain.source_assessment import SourceAssessmentResult
    from job_ftch.domain.source_spec import SourceSpec


@dataclass(frozen=True)
class SourceAssessmentContext:
    source_id: str


class SourceAssessmentAdapter(Protocol):
    """Assess a SourceSpec before normal ingestion starts."""

    name: str
    priority: int

    def supports(self, spec: SourceSpec) -> bool:
        """Return True when this adapter can assess the spec."""

    async def assess(
        self, spec: SourceSpec, context: SourceAssessmentContext
    ) -> SourceAssessmentResult:
        """Return freshness-related capabilities and evidence."""


def _is_assessment_stale(cached: SourceAssessmentResult, ttl_days: int) -> bool:
    """Return True if the cached assessment should be re-run."""
    now = datetime.now(UTC)
    assessed_at = cached.assessed_at
    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=UTC)
    age = now - assessed_at
    if age > timedelta(days=ttl_days):
        return True
    # Re-probe failed/blocked assessments after 1 day
    return bool(
        (cached.freshness.probe_failed or cached.freshness.probe_blocked)
        and age > timedelta(days=1)
    )


def _store_tenant_id(store: Store) -> str:
    tenant_id = getattr(store, "_tenant_id", None)
    return str(tenant_id) if tenant_id else "default"


class SourceAssessmentService:
    def __init__(self, adapters: list[SourceAssessmentAdapter]) -> None:
        self._adapters = sorted(adapters, key=lambda item: item.priority)

    async def assess(self, spec: SourceSpec) -> SourceAssessmentResult:
        source_id = source_spec_identifier(spec)
        context = SourceAssessmentContext(source_id=source_id)
        for adapter in self._adapters:
            if adapter.supports(spec):
                return await adapter.assess(spec, context)
        msg = f"No source assessment adapter supports source type: {spec.type}"
        raise ValueError(msg)

    async def assess_and_store(
        self,
        spec: SourceSpec,
        store: Store,
        *,
        force: bool = False,
        ttl_days: int = 7,
    ) -> SourceAssessmentResult:
        source_id = source_spec_identifier(spec)
        if not force:
            cached = await load_source_assessment(store, source_id)
            if cached is not None and not _is_assessment_stale(cached, ttl_days):
                return cached

        result = await self.assess(spec)
        await store_source_assessment(store, result)
        return result


async def load_source_assessment(store: Store, source_id: str) -> SourceAssessmentResult | None:

    return await store.get_source_assessment(_store_tenant_id(store), source_id)


async def store_source_assessment(store: Store, result: SourceAssessmentResult) -> None:
    await store.save_source_assessment(_store_tenant_id(store), result)


def create_source_assessment_service() -> SourceAssessmentService:
    from job_ftch.application.registry import get_source_assessment_adapters

    return SourceAssessmentService(get_source_assessment_adapters())


__all__ = [
    "SourceAssessmentAdapter",
    "SourceAssessmentContext",
    "SourceAssessmentService",
    "create_source_assessment_service",
    "load_source_assessment",
    "store_source_assessment",
]
