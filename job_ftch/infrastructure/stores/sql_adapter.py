"""DBMS-agnostic base for persistent stores."""

from __future__ import annotations

import abc
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from job_ftch.application.contracts import DedupReservation

from job_ftch.domain import (
    DuplicateRecord,
    ObservationLedgerEntry,
    OutboxRecord,
    OutboxState,
    RememberedDedupKey,
)
from job_ftch.domain.source_assessment import SourceAssessmentResult, SourceIngestState


def _ns(source_kind: str | None, source_name: str | None, key: str) -> str:
    if source_kind and source_name:
        return f"{source_kind}:{source_name}:{key}"
    return key


def _processed_timestamp_key(item_id: str) -> str:
    return f"processed_at:{item_id}"


def _is_processed_timestamp_fresh(raw_value: str | None, ttl_hours: int | None) -> bool:
    if ttl_hours is None:
        return raw_value is not None
    if raw_value is None:
        return False
    try:
        processed_at = datetime.fromisoformat(raw_value)
    except ValueError:
        return False
    if processed_at.tzinfo is None:
        processed_at = processed_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - processed_at <= timedelta(hours=ttl_hours)


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
    _SQL_SET_CLEAR: str
    _SQL_SET_CONTAINS: str
    _SQL_SET_MEMBERS: str
    _SQL_OUTBOX_ENQUEUE: str
    _SQL_OUTBOX_GET: str
    _SQL_OUTBOX_PENDING: str
    _SQL_OUTBOX_DELIVERED: str
    _SQL_DEDUP_CLAIM_ACQUIRE: str
    _SQL_DEDUP_CLAIM_OWNER: str
    _SQL_DEDUP_CLAIM_RELEASE: str
    _SQL_OBSERVATION_MAX_VERSION: str
    _SQL_OBSERVATION_INSERT: str
    _SQL_OBSERVATION_GET: str
    _SQL_SNAPSHOT_INSERT: str
    _SQL_SNAPSHOT_LAST_RUN_HASHES: str
    _SQL_SNAPSHOT_LAST_RUN_IDS: str
    _SQL_SNAPSHOT_PURGE: str
    _SQL_SNAPSHOT_PURGE_COUNT: str
    _SQL_SOURCE_ASSESSMENT_GET: str
    _SQL_SOURCE_ASSESSMENT_UPSERT: str
    _SQL_SOURCE_INGEST_STATE_GET: str
    _SQL_SOURCE_INGEST_STATE_UPSERT: str

    def __init__(self, *, processed_item_ttl_hours: int | None = 24) -> None:
        self._processed_item_ttl_hours = processed_item_ttl_hours

    @abc.abstractmethod
    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        """Execute a write query."""

    @abc.abstractmethod
    async def _execute_batch(self, sql: str, params_list: tuple[tuple[object, ...], ...]) -> None:
        """Execute a write query for multiple rows atomically."""

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

    async def clear_set(self, key: str) -> None:
        await self._execute(self._SQL_SET_CLEAR, (key,))

    async def set_add(self, key: str, member: str) -> None:
        await self._execute(self._SQL_SET_ADD, (key, member))

    async def set_contains(self, key: str, member: str) -> bool:
        row = await self._fetchone(self._SQL_SET_CONTAINS, (key, member))
        return bool(row)

    async def set_members(self, key: str) -> frozenset[str]:
        rows = await self._fetchall(self._SQL_SET_MEMBERS, (key,))
        return frozenset(str(row[0]) for row in rows)

    # Store implementation

    async def enqueue_outbox(self, record: OutboxRecord) -> OutboxRecord:
        await self._execute(
            self._SQL_OUTBOX_ENQUEUE,
            (
                record.outbox_id,
                record.tenant_id,
                record.idempotency_key,
                OutboxState.OUTBOXED.value,
                record.model_dump_json(),
            ),
        )
        row = await self._fetchone(self._SQL_OUTBOX_GET, (record.idempotency_key,))
        if row:
            outbox_rec = OutboxRecord.model_validate_json(str(row[0]))
            return outbox_rec.model_copy(update={"state": OutboxState(str(row[1]))})
        return record

    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[OutboxRecord, ...]:
        rows = await self._fetchall(
            self._SQL_OUTBOX_PENDING, (OutboxState.OUTBOXED.value, tenant_id or "default", limit)
        )
        return tuple(
            OutboxRecord.model_validate_json(str(row[0])).model_copy(
                update={"state": OutboxState(str(row[1]))}
            )
            for row in rows
        )

    async def mark_outbox_delivered(self, idempotency_key: str) -> OutboxRecord | None:
        await self._execute(
            self._SQL_OUTBOX_DELIVERED, (OutboxState.DELIVERED.value, idempotency_key)
        )
        row = await self._fetchone(self._SQL_OUTBOX_GET, (idempotency_key,))
        if row is None:
            return None
        record = OutboxRecord.model_validate_json(str(row[0]))
        return record.model_copy(update={"state": OutboxState(str(row[1]))})

    async def acquire_dedup_claim(self, key: str, owner_id: str, *, ttl_seconds: int) -> bool:
        await self._execute(
            self._SQL_DEDUP_CLAIM_ACQUIRE,
            (key, owner_id, ttl_seconds),
        )
        row = await self._fetchone(self._SQL_DEDUP_CLAIM_OWNER, (key,))
        return bool(row and str(row[0]) == owner_id)

    async def release_dedup_claim(self, key: str, owner_id: str) -> None:
        await self._execute(self._SQL_DEDUP_CLAIM_RELEASE, (key, owner_id))

    async def compare_and_reserve(
        self, keys: tuple[str, ...], owner_id: str, *, ttl_seconds: int
    ) -> DedupReservation:
        from job_ftch.application.contracts import DedupReservation

        for key in keys:
            acquired = await self.acquire_dedup_claim(key, owner_id, ttl_seconds=ttl_seconds)
            if not acquired:
                for prev_key in keys[: keys.index(key)]:
                    await self.release_dedup_claim(prev_key, owner_id)
                return DedupReservation(acquired=False, conflicting_key=key)
        return DedupReservation(acquired=True, reserved_keys=keys)

    async def record_observation(self, entry: ObservationLedgerEntry) -> ObservationLedgerEntry:
        existing = await self.get_observation(
            entry.stable_id, entry.content_hash, tenant_id=entry.tenant_id
        )
        if existing is not None:
            return existing
        import asyncio

        max_retries = 3
        for attempt in range(max_retries):
            row = await self._fetchone(
                self._SQL_OBSERVATION_MAX_VERSION,
                (entry.tenant_id, entry.stable_id),
            )
            previous = row[0] if row else 0
            version = int(str(previous or 0)) + 1
            recorded = entry.model_copy(update={"content_version": version})
            try:
                await self._execute(
                    self._SQL_OBSERVATION_INSERT,
                    (
                        entry.tenant_id,
                        entry.stable_id,
                        entry.content_hash,
                        version,
                        recorded.model_dump_json(),
                    ),
                )
                return recorded
            except Exception as e:
                err_str = str(e).lower()
                is_unique_violation = (
                    "unique" in err_str or "integrity" in err_str or "duplicate" in err_str
                )
                if attempt == max_retries - 1 or not is_unique_violation:
                    raise
                existing = await self.get_observation(
                    entry.stable_id,
                    entry.content_hash,
                    tenant_id=entry.tenant_id,
                )
                if existing is not None:
                    return existing
                await asyncio.sleep(0.01 * (attempt + 1))
        return recorded

    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> ObservationLedgerEntry | None:
        row = await self._fetchone(
            self._SQL_OBSERVATION_GET,
            (tenant_id, stable_id, content_hash),
        )
        return ObservationLedgerEntry.model_validate_json(str(row[0])) if row else None

    async def has_processed(self, item_id: str) -> bool:
        timestamp = await self.get(_processed_timestamp_key(item_id))
        if timestamp is not None:
            return _is_processed_timestamp_fresh(timestamp, self._processed_item_ttl_hours)
        return await self.set_contains("processed", item_id)

    async def mark_processed(self, item_id: str) -> None:
        await self.set_add("processed", item_id)
        await self.set(_processed_timestamp_key(item_id), datetime.now(UTC).isoformat())

    async def has_dedup_key(self, key: str) -> bool:
        return await self.set_contains("dedup_keys", key)

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        # 1. Add to global set of keys
        await self.set_add("dedup_keys", record.key)
        # 2. Add to kind-specific set of keys
        await self.set_add(f"dedup_keys:{record.kind.value}", record.key)
        # 3. Store the record itself
        await self.set(f"dedup_record:{record.key}", record.model_dump_json())

    async def get_dedup_key(self, key: str) -> RememberedDedupKey | None:
        raw = await self.get(f"dedup_record:{key}")
        if raw is None:
            return None
        return RememberedDedupKey.model_validate_json(raw)

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

    async def get_source_strategy(self, domain: str) -> dict[str, str] | None:
        import json

        raw = await self.get(f"strategy:{domain}")
        if raw:
            try:
                return cast("dict[str, str]", json.loads(raw))
            except Exception:
                return None
        return None

    async def save_source_strategy(self, domain: str, monitor: str, bypass: str) -> None:
        import json

        value = json.dumps({"monitor": monitor, "bypass": bypass})
        await self.set(f"strategy:{domain}", value)

    # Source snapshot methods (ADR-031)
    #
    # SQL is supplied by the concrete backend (SQLite / PostgreSQL) because
    # timestamp arithmetic and JSON column types differ between dialects.

    async def get_last_run_snapshot(
        self,
        tenant_id: str,
        source_id: str,
    ) -> frozenset[str]:
        rows = await self._fetchall(
            self._SQL_SNAPSHOT_LAST_RUN_IDS,
            (tenant_id, source_id, tenant_id, source_id),
        )
        return frozenset(str(row[0]) for row in rows)

    async def get_last_run_snapshot_hashes(self, tenant_id: str, source_id: str) -> dict[str, str]:
        rows = await self._fetchall(
            self._SQL_SNAPSHOT_LAST_RUN_HASHES,
            (tenant_id, source_id, tenant_id, source_id),
        )
        return {str(stable_id): str(content_hash) for stable_id, content_hash in rows}

    async def save_snapshot_rows(
        self,
        tenant_id: str,
        source_id: str,
        run_id: str,
        rows: tuple[tuple[str, str, str], ...],
    ) -> None:
        params_list = tuple(
            (tenant_id, source_id, run_id, stable_id, item_hash, item_json)
            for stable_id, item_hash, item_json in rows
        )
        if params_list:
            await self._execute_batch(self._SQL_SNAPSHOT_INSERT, params_list)

    async def purge_old_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        *,
        older_than_days: int,
    ) -> int:
        """Default impl for backends where DELETE doesn't return row count.

        Concrete stores that can return count from a single query (PostgreSQL)
        override this method.
        """
        if older_than_days <= 0:
            return 0
        await self._execute(
            self._SQL_SNAPSHOT_PURGE,
            (tenant_id, source_id, older_than_days),
        )
        row = await self._fetchone(
            self._SQL_SNAPSHOT_PURGE_COUNT,
            (),
        )
        if row is None:
            return 0
        try:
            return int(cast("int | str", row[0]))
        except (TypeError, ValueError):
            return 0

    async def get_source_assessment(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceAssessmentResult | None:
        row = await self._fetchone(
            self._SQL_SOURCE_ASSESSMENT_GET,
            (tenant_id, source_id),
        )
        if row is None or not row[0]:
            return None
        return SourceAssessmentResult.model_validate_json(str(row[0]))

    async def save_source_assessment(
        self,
        tenant_id: str,
        result: SourceAssessmentResult,
    ) -> None:
        await self._execute(
            self._SQL_SOURCE_ASSESSMENT_UPSERT,
            (
                tenant_id,
                result.source_id,
                result.source_type,
                result.schema_version,
                result.assessed_at,
                result.model_dump_json(),
            ),
        )

    async def get_source_ingest_state(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceIngestState | None:
        row = await self._fetchone(
            self._SQL_SOURCE_INGEST_STATE_GET,
            (tenant_id, source_id),
        )
        if row is None or not row[0]:
            return None
        return SourceIngestState.model_validate_json(str(row[0]))

    async def save_source_ingest_state(
        self,
        tenant_id: str,
        state: SourceIngestState,
    ) -> None:
        await self._execute(
            self._SQL_SOURCE_INGEST_STATE_UPSERT,
            (
                tenant_id,
                state.source_id,
                state.bootstrap_completed_at,
                state.model_dump_json(),
                state.updated_at,
            ),
        )
