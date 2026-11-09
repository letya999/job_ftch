"""Tenant-serialized continuation using immutable observation references."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from job_ftch.application.contracts import Store
    from job_ftch.application.pipeline import RunSummary
    from job_ftch.domain.observation import ObservationLedgerEntry


class ObservationContinuation:
    def __init__(self, store: Store, run_id: str, tenant_id: str) -> None:
        self.store = store
        self.key = f"llm_continuation:{run_id}"
        self.tenant_id = tenant_id
        self.references: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        raw = await self.store.get_run_state(self.key)
        if raw:
            rows = json.loads(raw)
            if not isinstance(rows, list) or any(
                not isinstance(row, list)
                or len(row) != 2
                or not all(isinstance(value, str) and value for value in row)
                for row in rows
            ):
                raise ValueError("Invalid LLM observation checkpoint")
            self.references = {tuple(row) for row in rows}

    async def add(self, entry: ObservationLedgerEntry) -> None:
        async with self._lock:
            reference = (entry.stable_id, entry.content_hash)
            if reference not in self.references:
                await self._save(self.references | {reference})

    async def complete(self, stable_id: str, content_hash: str) -> None:
        async with self._lock:
            reference = (stable_id, content_hash)
            if reference in self.references:
                await self._save(self.references - {reference})

    async def entries(self) -> list[ObservationLedgerEntry]:
        entries = []
        for stable_id, content_hash in sorted(self.references):
            entry = await self.store.get_observation(
                stable_id, content_hash, tenant_id=self.tenant_id
            )
            if entry is None:
                raise ValueError("Pending LLM observation is missing from the ledger")
            entries.append(entry)
        return entries

    async def _save(self, references: set[tuple[str, str]]) -> None:
        await self.store.set_run_state(self.key, json.dumps(sorted(references)))
        self.references = references

    def keep_pending(self, summary: RunSummary) -> None:
        if self.references and summary.completion_state != "waiting_llm":
            summary.completion_state = "waiting_llm"
            summary.llm_health_error = "continuation_retry"
            summary.next_retry_at = datetime.now(UTC) + timedelta(seconds=30)


observation_continuation: ContextVar[ObservationContinuation | None] = ContextVar(
    "observation_continuation", default=None
)


@contextmanager
def continuation_scope(store: Store, run_id: str, tenant_id: str) -> Iterator[None]:
    token = observation_continuation.set(ObservationContinuation(store, run_id, tenant_id))
    try:
        yield
    finally:
        observation_continuation.reset(token)


def requires_replay(result: dict[str, Any]) -> bool:
    return result["outcome"] in {"llm_wait", "failed"} or any(
        requires_replay(child) for child in result.get("children", [])
    )
