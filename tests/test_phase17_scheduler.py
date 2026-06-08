import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.application.pipeline import RunSummary
from job_ftch.application.scheduler import Scheduler
from job_ftch.config import Settings


@pytest.mark.asyncio
async def test_scheduler_runs_periodically():
    settings = Settings(schedule_interval_seconds=1)

    mock_run_summary = MagicMock(spec=RunSummary)
    mock_run_summary.emitted = 5
    mock_run_summary.finished_at = None

    run_fn = AsyncMock(return_value=mock_run_summary)

    scheduler = Scheduler(settings, run_fn)

    # Start scheduler in background
    task = asyncio.create_task(scheduler.run_forever())

    # Wait for a couple of runs
    await asyncio.sleep(2.5)

    # Stop scheduler
    scheduler._stop_event.set()
    await task

    # Should have run at least 2-3 times
    assert run_fn.call_count >= 2
    assert mock_run_summary.scheduled_run_index >= 2


@pytest.mark.asyncio
async def test_scheduler_respects_per_source_interval(tmp_path):
    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text("""
sources:
  - type: local_fixture
    path: fixtures/debug/raw_items.json
    interval_seconds: 2
""")

    settings = Settings(sources_file_path=sources_file, schedule_interval_seconds=10)

    run_fn = AsyncMock(return_value=MagicMock(spec=RunSummary))
    scheduler = Scheduler(settings, run_fn)

    interval = scheduler._calculate_interval()
    assert interval == 2


@pytest.mark.asyncio
async def test_scheduler_graceful_shutdown():
    settings = Settings(schedule_interval_seconds=100)

    run_fn = AsyncMock(return_value=MagicMock(spec=RunSummary))
    scheduler = Scheduler(settings, run_fn)

    task = asyncio.create_task(scheduler.run_forever())

    # Wait for first run to start
    await asyncio.sleep(0.1)

    # Stop immediately
    scheduler._stop_event.set()

    # Should finish quickly even though interval is 100s
    await asyncio.wait_for(task, timeout=1.0)

    assert run_fn.call_count == 1
