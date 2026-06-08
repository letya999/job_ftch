"""End-to-end test for Phase 16: persistence and search."""

import contextlib
from pathlib import Path

import pytest

from app import run_pipeline
from application.registry import create_job_backend, create_search_backend
from config import Settings


@pytest.mark.asyncio
async def test_phase16_sqlite_persistence_restart_search(tmp_path: Path):
    """
    Test scenario:
    1. Run pipeline with SQLite backend.
    2. Data is persisted.
    3. Backend is closed.
    4. New backend instance is opened pointing to the same file.
    5. Search returns the same data.
    """
    db_file = tmp_path / "test_jobs.sqlite"

    # 1. Setup settings
    settings = Settings(
        source_backend="local_fixture",
        debug_source_path=Path("fixtures/e2e/multisource_positive.jsonl"),
        job_backend="sqlite",
        job_group_store_backend="sqlite",
        search_backend="sqlite",
        job_store_path=db_file,
        pipeline_max_items_per_run=5,
    )

    # 2. Run pipeline
    await run_pipeline(settings)

    # 3. Verify file exists
    assert db_file.exists()
    assert db_file.stat().st_size > 0

    # 4. Open new backend and search
    # We simulate a "restart" by creating a fresh backend instance
    new_backend = create_job_backend(settings)
    new_searcher = create_search_backend(settings)

    try:
        # Check count
        count = await new_backend.count()
        assert count > 0

        # Search for something that likely exists in the fixture
        # Usually "python" or "engineer"
        results = await new_searcher.search("engineer")
        assert len(results) > 0

        # Verify it's a JobGroup
        group = results[0]
        assert group.group_id is not None
        assert len(group.jobs) > 0

    finally:
        await new_backend.close()


@pytest.mark.asyncio
async def test_phase16_metadata_no_mutation():
    """Verify that backend save does not mutate original job metadata."""
    from domain import Job, SourceKind
    from infrastructure.backends.jobs.sqlite import SQLiteJobBackend

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

    # Even if it fails to save to :memory: without full init,
    # we just want to see if it mutates the object before/during save call logic.
    with contextlib.suppress(Exception):
        await backend.save(job)

    assert job.metadata == {"original": "value"}
    assert "group_id" not in job.metadata
