from datetime import UTC, datetime

import pytest

from application.registry import create_job_backend
from config import Settings
from domain import Job, SourceKind


@pytest.fixture
def settings(tmp_path):
    return Settings(
        job_backend="sqlite", search_backend="sqlite", job_store_path=tmp_path / "test.db"
    )


@pytest.fixture
def sample_job():
    return Job(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="chan",
        title="Python Dev",
        company="Acme",
        description="Great job backend python",
        metadata={"fetched_at": datetime.now(UTC)},
    )


@pytest.mark.asyncio
async def test_sqlite_backend_save_get(settings, sample_job):
    backend = create_job_backend(settings)

    await backend.save(sample_job)

    saved_job = await backend.get_job(sample_job.stable_id)
    assert saved_job is not None
    assert saved_job.title == "Python Dev"

    # Check group
    groups = await backend.list_groups()
    assert len(groups) == 1
    assert groups[0].source_count == 1

    await backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_search(settings, sample_job):
    backend = create_job_backend(settings)

    await backend.save(sample_job)

    # Search by title
    res = await backend.search("python")
    assert len(res) == 1
    assert res[0].canonical_job.title == "Python Dev"

    # Search no match
    res2 = await backend.search("java")
    assert len(res2) == 0

    await backend.close()
