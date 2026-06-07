"""In-memory store for tests and local runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from application.registry import register_store

if TYPE_CHECKING:
    from config import Settings
    from domain import DuplicateRecord, RememberedDedupKey


def _ns(source_kind: str | None, source_name: str | None, key: str) -> str:
    if source_kind and source_name:
        return f"{source_kind}:{source_name}:{key}"
    return key


class InMemoryStore:
    def __init__(self) -> None:
        self._processed_ids: set[str] = set()
        self._dedup_keys: dict[str, RememberedDedupKey] = {}
        self._duplicate_records: list[DuplicateRecord] = []
        self._run_state: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        """StoreConnector implementation."""
        return self._run_state.get(key)

    async def set(self, key: str, value: str) -> None:
        """StoreConnector implementation."""
        self._run_state[key] = value

    async def delete(self, key: str) -> None:
        """StoreConnector implementation."""
        self._run_state.pop(key, None)

    async def set_add(self, key: str, member: str) -> None:
        """StoreConnector implementation."""
        self._sets.setdefault(key, set()).add(member)

    async def set_contains(self, key: str, member: str) -> bool:
        """StoreConnector implementation."""
        return member in self._sets.get(key, set())

    async def set_members(self, key: str) -> frozenset[str]:
        """StoreConnector implementation."""
        return frozenset(self._sets.get(key, set()))

    async def ping(self) -> bool:
        """StoreConnector implementation."""
        return True

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

    async def get_run_state(
        self,
        key: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> str | None:
        actual_key = _ns(source_kind, source_name, key)
        return self._run_state.get(actual_key)

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        actual_key = _ns(source_kind, source_name, key)
        self._run_state[actual_key] = value


@register_store("memory")
def _build_in_memory_store(settings: Settings) -> InMemoryStore:
    del settings
    return InMemoryStore()
