"""End-to-end test for Phase 16: persistence and search."""

from pathlib import Path

import pytest

from job_ftch.application.registry import create_job_backend, create_search_backend
from job_ftch.config import Settings
from job_ftch.domain import Job, SourceKind


@pytest.mark.asyncio
async def test_phase16_sqlite_persistence_restart_search(tmp_path: Path):
    """
    Test scenario:
    1. Directly save a job to SQLite backend.
    2. Backend is closed.
    3. New backend instance is opened pointing to the same file.
    4. Search returns the same data.
    """
    db_file = tmp_path / "test_jobs.sqlite"
    settings = Settings(
        job_backend="sqlite",
        job_group_store_backend="sqlite",
        search_backend="sqlite",
        job_store_path=db_file,
    )

    # 1. Manually save a job
    backend = create_job_backend(settings)
    job = Job(
        raw_item_id="item1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="Software Engineer",
        company="Tech Corp",
        description="Write code in Python",
    )
    await backend.save(job)
    await backend.close()

    # 2. Verify file exists
    assert db_file.exists()
    assert db_file.stat().st_size > 0

    # 3. Open new backend and search
    new_backend = create_job_backend(settings)
    new_searcher = create_search_backend(settings)

    try:
        # Check count
        count = await new_backend.count()
        assert count == 1

        # Search
        results = await new_searcher.search("engineer")
        assert len(results) == 1
        assert results[0].canonical_job.title == "Software Engineer"

    finally:
        await new_backend.close()


@pytest.mark.asyncio
async def test_phase16_metadata_no_mutation():
    """Verify that backend save does not mutate original job metadata."""
    from job_ftch.domain import Job, SourceKind
    from job_ftch.infrastructure.backends.jobs.sqlite import SQLiteJobBackend

    # Mock settings
    class MockSettings:
        job_store_path = Path(":memory:")
        store_path = Path(":memory:")

    backend = SQLiteJobBackend(MockSettings())

    job = Job(
        raw_item_id="item1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        title="Software Engineer",
        company="Tech Corp",
        description="Write code",
        metadata={"original": "value"},
    )

    # We just want to see if it mutates the object before/during save call logic.
    import contextlib

    with contextlib.suppress(Exception):
        await backend.save(job)

    assert job.metadata == {"original": "value"}
    assert "group_id" not in job.metadata
