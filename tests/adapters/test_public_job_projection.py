"""The public catalog avoids full group reads and repeated cached variants."""

from types import SimpleNamespace

import pytest

from job_ftch.adapters.telegram_bot.public_jobs import PublicJobsCache, build_public_jobs_response


@pytest.mark.asyncio
async def test_public_catalog_uses_compact_postgres_projection() -> None:
    class Store:
        calls: list[int] = []

        async def list_public_canonical_jobs(self, limit: int) -> list[object]:
            self.calls.append(limit)
            return [SimpleNamespace(group_id="g1", title="Engineer", company="Acme")]

    class Runner:
        def __init__(self) -> None:
            self.store = Store()

        def get_runtime(self, tenant_id: str) -> object:
            assert tenant_id == "ai_jobs"
            return SimpleNamespace(job_group_store=self.store)

        async def latest_jobs(self, tenant_id: str, *, limit: int) -> list[object]:
            return []

    runner = Runner()
    payload = await build_public_jobs_response(runner, "ai_jobs", limit=1)

    assert payload["job_count"] == 1
    assert payload["jobs"][0]["id"] == "g1"
    assert runner.store.calls == [1]


@pytest.mark.asyncio
async def test_public_catalog_reuses_one_cache_entry_for_different_limits() -> None:
    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        async def latest_jobs(self, tenant_id: str, *, limit: int) -> list[object]:
            self.calls += 1
            return [SimpleNamespace(group_id=str(i), title=str(i)) for i in range(3)]

    runner = Runner()
    cache = PublicJobsCache()
    one = await build_public_jobs_response(runner, "ai_jobs", limit=1, cache=cache)
    two = await build_public_jobs_response(runner, "ai_jobs", limit=2, cache=cache)

    assert one["job_count"] == 1
    assert two["job_count"] == 2
    assert runner.calls == 1
    assert len(cache.entries) == 1
