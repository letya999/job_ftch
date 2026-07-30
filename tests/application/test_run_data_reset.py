from __future__ import annotations

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


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

    counts = await store.clear_run_artifacts()

    assert counts["kv"] == len(removed)
    for key in preserved:
        assert await store.get_run_state(key) is not None
    for key in removed:
        assert await store.get_run_state(key) is None
