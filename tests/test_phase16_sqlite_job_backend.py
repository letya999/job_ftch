from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from job_ftch.application.registry import create_job_backend
from job_ftch.config import Settings
from job_ftch.domain import JobRecord, SourceKind

if TYPE_CHECKING:
    from job_ftch.infrastructure.backends.jobs.sqlite import SQLiteJobBackend


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        job_backend="sqlite", search_backend="sqlite", job_store_path=tmp_path / "test.db"
    )


@pytest.fixture
def sample_job() -> JobRecord:
    return JobRecord(
        raw_item_id="1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="chan",
        title="Python Dev",
        company="Acme",
        description="Great job backend python",
        metadata={"fetched_at": datetime.now(UTC)},
    )


@pytest.mark.asyncio
async def test_sqlite_backend_save_get(settings: Settings, sample_job: JobRecord) -> None:
    backend = cast("SQLiteJobBackend", create_job_backend(settings))

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
async def test_sqlite_backend_search(settings: Settings, sample_job: JobRecord) -> None:
    backend = cast("SQLiteJobBackend", create_job_backend(settings))

    await backend.save(sample_job)

    # Search by title
    res = await backend.search("python")
    assert len(res) == 1
    assert res[0].canonical_job.title == "Python Dev"

    # Search no match
    res2 = await backend.search("java")
    assert len(res2) == 0

    await backend.close()


@pytest.mark.asyncio
async def test_sqlite_backend_delete(settings: Settings, sample_job: JobRecord) -> None:
    backend = cast("SQLiteJobBackend", create_job_backend(settings))

    # Save two jobs that land in one group (same title and company, different source)
    job1 = sample_job
    job2 = sample_job.model_copy(update={"raw_item_id": "2", "source_name": "chan2"})

    await backend.save(job1)
    await backend.save(job2)

    # Assert group has 2 jobs
    groups = await backend.list_groups()
    assert len(groups) == 1
    assert groups[0].source_count == 2

    # Delete one job
    await backend.delete(job1.stable_id)

    # Assert group has 1 job
    updated_group = await backend.get_group(groups[0].group_id)
    assert updated_group is not None
    assert updated_group.source_count == 1
    assert len(updated_group.jobs) == 1
    assert updated_group.jobs[0].stable_id == job2.stable_id

    # Assert find_by_fingerprint works for remaining, and no longer returns deleted job's stale group (if it were different, but here it's same group)
    # Actually, if we deleted the only job that had a certain URL/FP, we should check that.
    # But here they have same FP.

    # Assert get_job(deleted_id) returns None
    assert await backend.get_job(job1.stable_id) is None

    await backend.close()
