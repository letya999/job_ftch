"""In-memory store for tests and local runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class InMemoryStore:
    def __init__(self) -> None:
        self._processed_ids: set[str] = set()
        self._dedup_keys: set[str] = set()
        self._run_state: dict[str, str] = {}
        self._run_summaries: dict[str, Mapping[str, object]] = {}
        self._rejections: dict[str, Mapping[str, object]] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed_ids

    async def mark_processed(self, item_id: str) -> None:
        await self.try_mark_processed(item_id)

    async def try_mark_processed(self, item_id: str) -> bool:
        if item_id in self._processed_ids:
            return False
        self._processed_ids.add(item_id)
        return True

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup_keys

    async def remember_dedup_key(self, key: str) -> None:
        await self.try_remember_dedup_key(key)

    async def try_remember_dedup_key(
        self,
        key: str,
        *,
        kind: str = "generic",
        item_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        _ = (kind, item_id, reason)
        if key in self._dedup_keys:
            return False
        self._dedup_keys.add(key)
        return True

    async def get_run_state(self, key: str) -> str | None:
        return self._run_state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self._run_state[key] = value

    async def get_source_cursor(self, source_key: str) -> str | None:
        return await self.get_run_state(source_key)

    async def set_source_cursor(self, source_key: str, cursor_value: str) -> None:
        await self.set_run_state(source_key, cursor_value)

    async def save_run_summary(self, run_id: str, payload: Mapping[str, object]) -> None:
        self._run_summaries[run_id] = dict(payload)

    async def save_rejection(
        self,
        rejection_id: str,
        *,
        run_id: str | None,
        stage: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> None:
        self._rejections[rejection_id] = {
            "run_id": run_id,
            "stage": stage,
            "reason": reason,
            "payload": dict(payload),
        }
