"""In-memory store for tests and local runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from application.registry import register_store
from domain import DuplicateRecord, RememberedDedupKey

if TYPE_CHECKING:
    from config import Settings


def _ns(source_kind: str | None, source_name: str | None, key: str) -> str:
    if source_kind and source_name:
        return f"{source_kind}:{source_name}:{key}"
    return key


class InMemoryStore:
    """In-memory Store + StoreConnector backed by a single unified KV/set pair.

    Store methods are implemented on top of StoreConnector primitives so both
    interfaces see the same underlying data — no split-brain between callers
    of has_processed() and set_members("processed").
    """

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    # StoreConnector primitives

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)

    async def set_add(self, key: str, member: str) -> None:
        self._sets.setdefault(key, set()).add(member)

    async def set_contains(self, key: str, member: str) -> bool:
        return member in self._sets.get(key, set())

    async def set_members(self, key: str) -> frozenset[str]:
        return frozenset(self._sets.get(key, set()))

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    # Store methods — built on top of StoreConnector primitives

    async def has_processed(self, item_id: str) -> bool:
        return await self.set_contains("processed", item_id)

    async def mark_processed(self, item_id: str) -> None:
        await self.set_add("processed", item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return await self.set_contains("dedup_keys", key)

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        await self.set_add("dedup_keys", record.key)
        await self.set_add(f"dedup_keys:{record.kind.value}", record.key)
        await self.set(f"dedup_record:{record.key}", record.model_dump_json())

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        set_key = "dedup_keys" if kind is None else f"dedup_keys:{kind}"
        members = await self.set_members(set_key)
        results = []
        for m in sorted(members):
            raw = await self.get(f"dedup_record:{m}")
            if raw:
                results.append(RememberedDedupKey.model_validate_json(raw))
        return tuple(results)

    async def record_duplicate(self, record: DuplicateRecord) -> None:
        await self.set_add("dup_records", record.item_id)
        await self.set(f"dup_record:{record.item_id}", record.model_dump_json())

    async def list_duplicate_records(self) -> tuple[DuplicateRecord, ...]:
        members = await self.set_members("dup_records")
        results = []
        for m in sorted(members):
            raw = await self.get(f"dup_record:{m}")
            if raw:
                results.append(DuplicateRecord.model_validate_json(raw))
        return tuple(results)

    async def get_run_state(
        self,
        key: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> str | None:
        return await self.get(_ns(source_kind, source_name, key))

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        await self.set(_ns(source_kind, source_name, key), value)


@register_store("memory")
def _build_in_memory_store(settings: Settings) -> InMemoryStore:
    del settings
    return InMemoryStore()
