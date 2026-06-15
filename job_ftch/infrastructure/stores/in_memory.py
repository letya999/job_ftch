"""In-memory store for tests and local runs."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, cast

from job_ftch.application.registry import register_store
from job_ftch.domain import DuplicateRecord, RememberedDedupKey

if TYPE_CHECKING:
    from job_ftch.config import Settings


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

    def __init__(
        self,
        *,
        max_keys: int = 50_000,
        max_set_members: int = 50_000,
    ) -> None:
        self._max_keys = max(max_keys, 1)
        self._max_set_members = max(max_set_members, 1)
        self._kv: OrderedDict[str, str] = OrderedDict()
        self._sets: dict[str, OrderedDict[str, None]] = {}

    def _trim_kv(self) -> None:
        while len(self._kv) > self._max_keys:
            self._kv.popitem(last=False)

    def _trim_set(self, key: str) -> None:
        members = self._sets.get(key)
        if members is None:
            return
        while len(members) > self._max_set_members:
            members.popitem(last=False)

    # StoreConnector primitives

    async def get(self, key: str) -> str | None:
        value = self._kv.get(key)
        if value is not None:
            self._kv.move_to_end(key)
        return value

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value
        self._kv.move_to_end(key)
        self._trim_kv()

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)

    async def clear_set(self, key: str) -> None:
        self._sets.pop(key, None)

    async def set_add(self, key: str, member: str) -> None:
        members = self._sets.setdefault(key, OrderedDict())
        members[member] = None
        members.move_to_end(member)
        self._trim_set(key)

    async def set_contains(self, key: str, member: str) -> bool:
        return member in self._sets.get(key, {})

    async def set_members(self, key: str) -> frozenset[str]:
        members = self._sets.get(key)
        if members is None:
            return frozenset()
        return frozenset(members)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    async def reset_namespace(self, prefix: str) -> None:
        self._kv = OrderedDict(
            (key, value) for key, value in self._kv.items() if not key.startswith(prefix)
        )
        self._sets = {key: value for key, value in self._sets.items() if not key.startswith(prefix)}

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

    async def get_source_strategy(self, domain: str) -> dict[str, str] | None:
        import json

        raw = await self.get(f"strategy:{domain}")
        if raw is None:
            return None
        return cast("dict[str, str]", json.loads(raw))

    async def save_source_strategy(self, domain: str, monitor: str, bypass: str) -> None:
        import json

        await self.set(f"strategy:{domain}", json.dumps({"monitor": monitor, "bypass": bypass}))


@register_store("memory")
def _build_in_memory_store(settings: Settings) -> InMemoryStore:
    return InMemoryStore(
        max_keys=settings.memory_max_keys,
        max_set_members=settings.memory_max_set_members,
    )
