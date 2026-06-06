"""In-memory store for tests and local runs."""

from __future__ import annotations


class InMemoryStore:
    def __init__(self) -> None:
        self._processed_ids: set[str] = set()
        self._dedup_keys: set[str] = set()
        self._run_state: dict[str, str] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed_ids

    async def mark_processed(self, item_id: str) -> None:
        self._processed_ids.add(item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup_keys

    async def remember_dedup_key(self, key: str) -> None:
        self._dedup_keys.add(key)

    async def get_run_state(self, key: str) -> str | None:
        return self._run_state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self._run_state[key] = value
