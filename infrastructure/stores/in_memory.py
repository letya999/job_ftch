"""In-memory store for tests and local runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain import DuplicateRecord, RememberedDedupKey


class InMemoryStore:
    def __init__(self) -> None:
        self._processed_ids: set[str] = set()
        self._dedup_keys: dict[str, RememberedDedupKey] = {}
        self._duplicate_records: list[DuplicateRecord] = []
        self._run_state: dict[str, str] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed_ids

    async def mark_processed(self, item_id: str) -> None:
        self._processed_ids.add(item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup_keys

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        self._dedup_keys[record.key] = record

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        records = tuple(self._dedup_keys.values())
        if kind is None:
            return records
        return tuple(record for record in records if record.kind.value == kind)

    async def record_duplicate(self, record: DuplicateRecord) -> None:
        self._duplicate_records.append(record)

    async def list_duplicate_records(self) -> tuple[DuplicateRecord, ...]:
        return tuple(self._duplicate_records)

    async def get_run_state(self, key: str) -> str | None:
        return self._run_state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self._run_state[key] = value
