import pytest

from job_ftch.domain import JobRecord, SourceKind
from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore


@pytest.mark.asyncio
async def test_in_memory_store_clear():
    store = InMemoryJobGroupStore()
    job = JobRecord(
        stable_id="job1",
        raw_item_id="raw1",
        title="Software Engineer",
        company="Google",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="tg",
        description="desc",
        canonical_url="http://google.com/job1",
    )

    await store.create(job)
    assert await store.count() == 1

    count = await store.clear()
    assert count == 1
    assert await store.count() == 0
    assert await store.find_by_url("http://google.com/job1") is None
