from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_ftch.application.pipeline import RunSummary
from job_ftch.application.scheduler import Scheduler
from job_ftch.config import Settings


@pytest.mark.anyio
async def test_scheduler_ticks_and_stops() -> None:
    settings = Settings(schedule_interval_seconds=1)

    mock_summary = MagicMock(spec=RunSummary)
    mock_summary.emitted = 1
    run_fn = AsyncMock(return_value=mock_summary)

    scheduler = Scheduler(settings, run_fn)

    # Start in background
    task = asyncio.create_task(scheduler.run_forever())

    # Give it time to run at least once and create PID file
    await asyncio.sleep(0.5)

    pid_file = Path(".runtime/.pid")
    assert pid_file.exists()
    assert pid_file.read_text() == str(os.getpid())

    # Wait for more ticks
    await asyncio.sleep(1.5)

    scheduler.stop()
    await asyncio.wait_for(task, timeout=5)

    assert run_fn.call_count >= 2
    assert not pid_file.exists()


@pytest.mark.anyio
async def test_scheduler_signal_stop() -> None:
    # Skip on Windows for real signal testing if needed,
    # but Scheduler handles SIGINT via loop.add_signal_handler which might work in some envs.
    # However, sending signals to itself on Windows is tricky.

    settings = Settings(schedule_interval_seconds=10)
    run_fn = AsyncMock(return_value=MagicMock(spec=RunSummary))
    scheduler = Scheduler(settings, run_fn)

    task = asyncio.create_task(scheduler.run_forever())
    await asyncio.sleep(0.5)

    assert Path(".runtime/.pid").exists()

    # Instead of os.kill, we can just trigger the event directly or mock the signal
    # But let's try to be realistic where possible
    if os.name != "nt":
        os.kill(os.getpid(), signal.SIGINT)
    else:
        # Fallback for Windows: just set the event
        scheduler.stop()

    await asyncio.wait_for(task, timeout=5)
    assert not Path(".runtime/.pid").exists()
