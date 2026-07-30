"""Store-backed queue for work that must not delay or change policy."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from job_ftch.domain import EnrichmentTask

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store


class PostAcceptEnrichmentQueue:
    _INDEX_KEY = "enrichment:pending:index"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def enqueue(self, task: EnrichmentTask) -> EnrichmentTask:
        key = f"enrichment:task:{task.task_id}"
        if await self._store.get_run_state(key) is None:
            await self._store.set_run_state(key, task.model_dump_json())
            raw = await self._store.get_run_state(self._INDEX_KEY)
            ids = [] if not raw else json.loads(raw)
            if task.task_id not in ids:
                ids.append(task.task_id)
                await self._store.set_run_state(self._INDEX_KEY, json.dumps(ids))
        return task

    async def list_pending(self, limit: int = 100) -> tuple[EnrichmentTask, ...]:
        raw = await self._store.get_run_state(self._INDEX_KEY)
        if not raw:
            return ()
        result: list[EnrichmentTask] = []
        for task_id in json.loads(raw):
            if len(result) >= limit:
                break
            payload = await self._store.get_run_state(f"enrichment:task:{task_id}")
            if payload:
                task = EnrichmentTask.model_validate_json(payload)
                if task.status == "pending":
                    result.append(task)
        return tuple(result)
