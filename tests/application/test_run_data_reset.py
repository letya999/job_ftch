from __future__ import annotations

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


def _assessment(source_id: str = "career_site:test") -> SourceAssessmentResult:
    return SourceAssessmentResult(
        source_id=source_id,
        source_type="career_site",
        capabilities=SourceCapabilities(has_publication_time=True),
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=True,
            can_filter_since_yesterday=True,
            item_level_dates=True,
            requires_full_snapshot=False,
            rationale="clear_run_artifacts",
        ),
    )


@pytest.mark.asyncio
async def test_clear_run_artifacts_preserves_profiles_sources_and_history() -> None:
    base = InMemoryStore()
    store = TenantStore("ai_jobs", base)
    preserved = {
        "runtime_source:career_site:test": "source",
        "candidate_profile:user:profile": "profile",
        "active_candidate_profile:user": "profile",
        "run_history:new": "history",
    }
    removed = {
        "relevance:v4:item": "cached",
        "presentable:item": "cached",
        "enrichment:task:one": "task",
        "pipeline.run_summary": "summary",
        "source_health:career_site:test": "health",
        "bot_publish:sent_ids": '["job-1"]',
        "bot_scheduler:last_publish_sent": "1",
        "bot_scheduler:last_publish_error": "rate limit",
        "bot_scheduler:pending_publish_since": "2026-07-29T10:00:00+00:00",
    }
    for key, value in {**preserved, **removed}.items():
        await store.set_run_state(key, value)
    await store.save_source_assessment("ai_jobs", _assessment())
    other = TenantStore("other_tenant", base)
    other_assessment = _assessment("career_site:other")
    await other.save_source_assessment("other_tenant", other_assessment)

    counts = await store.clear_run_artifacts()

    assert counts["kv"] == len(removed)
    assert counts["source_assessments"] == 1
    assert await store.get_source_assessment("ai_jobs", "career_site:test") is None
    assert (
        await other.get_source_assessment("other_tenant", "career_site:other") == other_assessment
    )
    for key in preserved:
        assert await store.get_run_state(key) is not None
    for key in removed:
        assert await store.get_run_state(key) is None
