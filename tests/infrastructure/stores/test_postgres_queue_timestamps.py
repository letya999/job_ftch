from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from job_ftch.domain import IngestTask
from job_ftch.infrastructure.stores.postgres import PostgreSQLStore
from job_ftch.infrastructure.stores.sql_adapter import SQLStoreAdapter


@pytest.mark.asyncio
async def test_postgres_queue_writes_native_timestamps() -> None:
    store = PostgreSQLStore("postgresql://example")
    execute = AsyncMock()
    store._execute = execute
    store._fetchone = AsyncMock(return_value=None)
    now = datetime(2026, 10, 2, 7, 50, tzinfo=timezone(timedelta(hours=3)))
    task = IngestTask(
        task_id="task",
        tenant_id="tenant",
        run_id="run",
        source_id="source",
        rate_scope="scope",
        available_at=now,
    )
    with pytest.raises(RuntimeError, match="disappeared"):
        await store.enqueue_ingest_task(task)
    await store.complete_ingest_task("task", "worker")
    await store.defer_ingest_task(task, "worker", available_at=now)
    await store.fail_ingest_task(task, "worker")
    await store.record_ingest_rate_limit(
        "scope", cooldown_until=now, retry_after_seconds=30, status_code=429
    )
    timestamp_positions = ((13, 17, 18), (1, 2), (1, 3), (2, 3), (1, 4))
    for call, positions in zip(execute.call_args_list, timestamp_positions, strict=True):
        params = call.args[1]
        for position in positions:
            assert isinstance(params[position], datetime)
            assert params[position].tzinfo == UTC
    assert execute.call_args_list[-1].args[1][1] == now.astimezone(UTC)
    assert store._queue_time(None) is None
    assert SQLStoreAdapter._queue_time(now) == now.astimezone(UTC).isoformat()
