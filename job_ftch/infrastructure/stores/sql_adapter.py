"""DBMS-agnostic base for persistent stores."""

from __future__ import annotations

import abc

from job_ftch.domain import DuplicateRecord, RememberedDedupKey


def _ns(source_kind: str | None, source_name: str | None, key: str) -> str:
    if source_kind and source_name:
        return f"{source_kind}:{source_name}:{key}"
    return key


class SQLStoreAdapter(abc.ABC):
    """DBMS-agnostic base for persistent stores.

    Implements Store (domain port) and StoreConnector (infra port) using
    abstract SQL + execute primitives.
    """

    # Abstract SQL constants to be overridden by concrete stores
    _SQL_KV_GET: str
    _SQL_KV_UPSERT: str
    _SQL_KV_DELETE: str
    _SQL_SET_ADD: str
    _SQL_SET_CONTAINS: str
    _SQL_SET_MEMBERS: str

    @abc.abstractmethod
    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        """Execute a write query."""

    @abc.abstractmethod
    async def _fetchone(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        """Fetch a single row."""

    @abc.abstractmethod
    async def _fetchall(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        """Fetch all rows."""

    @abc.abstractmethod
    async def _initialize(self) -> None:
        """Apply schema migrations."""

    @abc.abstractmethod
    async def ping(self) -> bool:
        """Check connection health."""

    # StoreConnector implementation

    async def get(self, key: str) -> str | None:
        row = await self._fetchone(self._SQL_KV_GET, (key,))
        return str(row[0]) if row else None

    async def set(self, key: str, value: str) -> None:
        await self._execute(self._SQL_KV_UPSERT, (key, value))

    async def delete(self, key: str) -> None:
        await self._execute(self._SQL_KV_DELETE, (key,))

    async def set_add(self, key: str, member: str) -> None:
        await self._execute(self._SQL_SET_ADD, (key, member))

    async def set_contains(self, key: str, member: str) -> bool:
        row = await self._fetchone(self._SQL_SET_CONTAINS, (key, member))
        return bool(row)

    async def set_members(self, key: str) -> frozenset[str]:
        rows = await self._fetchall(self._SQL_SET_MEMBERS, (key,))
        return frozenset(str(row[0]) for row in rows)

    # Store implementation

    async def has_processed(self, item_id: str) -> bool:
        return await self.set_contains("processed", item_id)

    async def mark_processed(self, item_id: str) -> None:
        await self.set_add("processed", item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return await self.set_contains("dedup_keys", key)

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        # 1. Add to global set of keys
        await self.set_add("dedup_keys", record.key)
        # 2. Add to kind-specific set of keys
        await self.set_add(f"dedup_keys:{record.kind.value}", record.key)
        # 3. Store the record itself
        await self.set(f"dedup_record:{record.key}", record.model_dump_json())

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        key = "dedup_keys" if kind is None else f"dedup_keys:{kind}"
        members = await self.set_members(key)
        results = []
        for m in sorted(members):  # Sort for stability
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
        actual_key = _ns(source_kind, source_name, key)
        return await self.get(actual_key)

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        actual_key = _ns(source_kind, source_name, key)
        await self.set(actual_key, value)
