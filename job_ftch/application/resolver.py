"""Small store-backed deferred resolver queue.

It uses the existing Store run-state port so SQLite/Postgres/fallback stores do
not need a second queue dependency. A later worker can claim tasks by updating
the same versioned record.
"""

from __future__ import annotations

import json
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from job_ftch.domain import ResolutionTask

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store


class DeferredResolverQueue:
    _INDEX_KEY = "resolver:pending:index"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def enqueue(self, task: ResolutionTask) -> ResolutionTask:
        key = f"resolver:task:{task.task_id}"
        existing = await self._store.get_run_state(key)
        if existing is None:
            await self._store.set_run_state(key, task.model_dump_json())
            index_raw = await self._store.get_run_state(self._INDEX_KEY)
            ids = [] if not index_raw else json.loads(index_raw)
            if task.task_id not in ids:
                ids.append(task.task_id)
                await self._store.set_run_state(self._INDEX_KEY, json.dumps(ids))
        return task

    async def list_pending(self, limit: int = 100) -> tuple[ResolutionTask, ...]:
        index_raw = await self._store.get_run_state(self._INDEX_KEY)
        if not index_raw:
            return ()
        result: list[ResolutionTask] = []
        for task_id in json.loads(index_raw):
            if len(result) >= limit:
                break
            raw = await self._store.get_run_state(f"resolver:task:{task_id}")
            if not raw:
                continue
            task = ResolutionTask.model_validate_json(raw)
            if task.status == "pending":
                result.append(task)
        return tuple(result)

    async def mark_complete(self, task_id: str) -> None:
        raw = await self._store.get_run_state(f"resolver:task:{task_id}")
        if not raw:
            return
        task = ResolutionTask.model_validate_json(raw)
        await self._store.set_run_state(
            f"resolver:task:{task_id}",
            task.model_copy(update={"status": "complete"}).model_dump_json(),
        )

    async def mark_retryable(self, task_id: str, *, attempt: int, not_before: datetime) -> None:
        """Persist a retry without creating a second task or delivery intent."""
        raw = await self._store.get_run_state(f"resolver:task:{task_id}")
        if not raw:
            return
        task = ResolutionTask.model_validate_json(raw)
        await self._store.set_run_state(
            f"resolver:task:{task_id}",
            task.model_copy(
                update={"attempt": attempt, "not_before": not_before, "status": "pending"}
            ).model_dump_json(),
        )
