"""Tenant-scoped wrapper around a generic `Store` (extracted from tenant_runner).

This file was extracted from `job_ftch/application/tenant_runner.py` per the
god-object split in the v0.0.4 MVP cleanup (Wave 2.2). The implementation is
verbatim: same signatures, same key format, same store/connector access. Only
the helpers `_source_health_key`, `_runtime_source_key`, `_source_disabled_key`,
`_candidate_profile_key`, `_active_candidate_profile_key`,
`_active_candidate_profiles_key`, `_json_default`, and `_summary_from_payload`
were moved alongside the class.

The class is re-exported from `tenant_runner` for backward compatibility:

    from job_ftch.application.tenant_runner import TenantStore  # works
    from job_ftch.application.tenant_store import TenantStore   # canonical
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.pipeline import RunSummary
from job_ftch.application.watermark import IncrementalCursor
from job_ftch.domain import (
    ManagedCandidateProfile,
    ObservationLedgerEntry,
    OutboxRecord,
    RuntimeSourceRecord,
    SourceHealth,
)

if TYPE_CHECKING:
    import builtins

    from job_ftch.application.contracts import Store, StoreConnector
    from job_ftch.domain.source_assessment import SourceAssessmentResult, SourceIngestState


logger = structlog.get_logger(__name__)


def _summary_sort_key(summary: RunSummary) -> tuple[str, str]:
    finished = summary.finished_at
    started = summary.started_at
    finished_key = finished.isoformat() if isinstance(finished, datetime) else str(finished or "")
    started_key = started.isoformat() if isinstance(started, datetime) else str(started or "")
    return (finished_key, started_key)


def _source_health_key(source_id: str) -> str:
    return f"source_health:{source_id}"


def _runtime_source_key(source_id: str) -> str:
    return f"runtime_source:{source_id}"


def _source_disabled_key(source_id: str) -> str:
    return f"source_disabled:{source_id}"


def _candidate_profile_key(user_id: str, profile_id: str) -> str:
    return f"candidate_profile:{user_id}:{profile_id}"


def _active_candidate_profile_key(user_id: str) -> str:
    return f"active_candidate_profile:{user_id}"


def _active_candidate_profiles_key(user_id: str) -> str:
    return f"active_candidate_profile_set:{user_id}"


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    return str(value)


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


def _summary_from_payload(payload: dict[str, Any], *, tenant_id: str | None = None) -> RunSummary:
    for key in ("started_at", "finished_at"):
        value = payload.get(key)
        if isinstance(value, str):
            with suppress(ValueError):
                payload[key] = datetime.fromisoformat(value)
    summary = RunSummary(**payload)
    if tenant_id is not None:
        summary.tenant_id = tenant_id
    return summary


class TenantStore:
    """Store wrapper that prefixes every key space with a tenant slug."""

    def __init__(self, tenant_id: str, store: Store) -> None:
        self._tenant_id = tenant_id
        self._store = store

    def _key(self, key: str) -> str:
        return f"{self._tenant_id}:{key}"

    def _scope_outbox_record(self, record: OutboxRecord) -> OutboxRecord:
        scoped_key = sha256(f"{self._tenant_id}:{record.idempotency_key}".encode()).hexdigest()
        return record.model_copy(
            update={
                "outbox_id": scoped_key,
                "idempotency_key": scoped_key,
                "tenant_id": self._tenant_id,
            }
        )

    async def enqueue_outbox(self, record: OutboxRecord) -> OutboxRecord:
        return await self._store.enqueue_outbox(self._scope_outbox_record(record))

    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[OutboxRecord, ...]:
        if tenant_id is not None and tenant_id != self._tenant_id:
            raise ValueError(f"tenant_id mismatch: {tenant_id} != {self._tenant_id}")
        return await self._store.list_pending_outbox(limit, tenant_id=self._tenant_id)

    async def mark_outbox_delivered(self, idempotency_key: str) -> OutboxRecord | None:
        return await self._store.mark_outbox_delivered(idempotency_key)

    async def acquire_dedup_claim(self, key: str, owner_id: str, *, ttl_seconds: int) -> bool:
        return await self._store.acquire_dedup_claim(
            self._key(key), owner_id, ttl_seconds=ttl_seconds
        )

    async def release_dedup_claim(self, key: str, owner_id: str) -> None:
        await self._store.release_dedup_claim(self._key(key), owner_id)

    def _run_state_key(
        self,
        key: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> str:
        prefix = self._key(key)
        if source_kind and source_name:
            return f"{self._tenant_id}:{source_kind}:{source_name}:{key}"
        return prefix

    def _snapshot_key(self, source_id: str) -> str:
        return self._key(f"snapshot:{source_id}:latest")

    async def get(self, key: str) -> str | None:
        connector = cast("StoreConnector", self._store)
        return await connector.get(self._key(key))

    async def set(self, key: str, value: str) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set(self._key(key), value)

    async def delete(self, key: str) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.delete(self._key(key))

    async def load_source_snapshot(self, source_id: str) -> dict[str, str]:
        """Return stable_id -> content_hash mapping from the latest snapshot."""
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._snapshot_key(source_id))
        if not raw:
            return {}
        try:
            import json

            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    async def save_source_snapshot(self, source_id: str, snapshot: dict[str, str]) -> None:
        """Persist the latest snapshot mapping for a source."""
        connector = cast("StoreConnector", self._store)
        import json

        await connector.set(self._snapshot_key(source_id), json.dumps(snapshot, sort_keys=True))

    # ADR-031: tenant-scoped delegation to underlying store snapshot methods.
    # Signatures match the underlying ``Store`` protocol (taking ``tenant_id``)
    # so ``TenantStore`` remains structurally compatible with ``Store`` at
    # the type-checker level.
    async def get_last_run_snapshot(
        self,
        tenant_id: str,
        source_id: str,
    ) -> frozenset[str]:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        return await self._store.get_last_run_snapshot(self._tenant_id, source_id)

    async def get_last_run_snapshot_hashes(self, tenant_id: str, source_id: str) -> dict[str, str]:
        if tenant_id != self._tenant_id:
            raise ValueError(f"tenant_id mismatch: {tenant_id} != {self._tenant_id}")
        return await self._store.get_last_run_snapshot_hashes(self._tenant_id, source_id)

    async def save_snapshot_rows(
        self,
        tenant_id: str,
        source_id: str,
        run_id: str,
        rows: tuple[tuple[str, str, str], ...],
    ) -> None:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        await self._store.save_snapshot_rows(self._tenant_id, source_id, run_id, rows)

    async def purge_old_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        *,
        older_than_days: int,
    ) -> int:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        return await self._store.purge_old_snapshots(
            self._tenant_id, source_id, older_than_days=older_than_days
        )

    async def get_source_assessment(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceAssessmentResult | None:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        return await self._store.get_source_assessment(self._tenant_id, source_id)

    async def save_source_assessment(
        self,
        tenant_id: str,
        result: SourceAssessmentResult,
    ) -> None:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        await self._store.save_source_assessment(self._tenant_id, result)

    async def get_source_ingest_state(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceIngestState | None:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        return await self._store.get_source_ingest_state(self._tenant_id, source_id)

    async def save_source_ingest_state(
        self,
        tenant_id: str,
        state: SourceIngestState,
    ) -> None:
        if tenant_id != self._tenant_id:
            msg = f"tenant_id mismatch: {tenant_id} != {self._tenant_id}"
            raise ValueError(msg)
        await self._store.save_source_ingest_state(self._tenant_id, state)

    async def has_processed(self, item_id: str) -> bool:
        from job_ftch.config import get_settings

        connector = cast("StoreConnector", self._store)
        ttl_hours = get_settings().processed_item_ttl_hours
        timestamp = await connector.get(self._key(_processed_timestamp_key(item_id)))
        if timestamp is not None:
            return _is_processed_timestamp_fresh(str(timestamp), ttl_hours)
        return bool(await connector.set_contains(self._key("processed"), item_id))

    async def record_observation(self, entry: ObservationLedgerEntry) -> ObservationLedgerEntry:
        return await self._store.record_observation(
            entry.model_copy(update={"tenant_id": self._tenant_id})
        )

    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> ObservationLedgerEntry | None:
        del tenant_id
        return await self._store.get_observation(stable_id, content_hash, tenant_id=self._tenant_id)

    async def mark_processed(self, item_id: str) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set_add(self._key("processed"), item_id)
        await connector.set(
            self._key(_processed_timestamp_key(item_id)),
            datetime.now(UTC).isoformat(),
        )

    async def has_dedup_key(self, key: str) -> bool:
        connector = cast("StoreConnector", self._store)
        return bool(await connector.set_contains(self._key("dedup_keys"), key))

    async def remember_dedup_key(self, record: Any) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set_add(self._key("dedup_keys"), record.key)
        await connector.set_add(self._key(f"dedup_keys:{record.kind.value}"), record.key)
        await connector.set(self._key(f"dedup_record:{record.key}"), record.model_dump_json())

    async def get_dedup_key(self, key: str) -> Any | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(f"dedup_record:{key}"))
        if raw is None:
            return None
        from job_ftch.domain import RememberedDedupKey

        return RememberedDedupKey.model_validate_json(raw)

    async def clear_dedup_state(self) -> int:
        """Clear all processed markers and dedup keys for this tenant. Returns count of deleted dedup records."""
        connector = cast("StoreConnector", self._store)
        # Clear the processed-item set
        processed_members = await connector.set_members(self._key("processed"))
        await connector.clear_set(self._key("processed"))
        for member in processed_members:
            await connector.delete(self._key(_processed_timestamp_key(member)))
        # List dedup keys before clearing
        dedup_members = await connector.set_members(self._key("dedup_keys"))
        # Delete individual dedup record KV entries
        for member in dedup_members:
            await connector.delete(self._key(f"dedup_record:{member}"))
        # Clear all dedup set indices
        await connector.clear_set(self._key("dedup_keys"))
        for kind in ("url", "fingerprint", "content"):
            await connector.clear_set(self._key(f"dedup_keys:{kind}"))
        return len(dedup_members)

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[Any, ...]:
        connector = cast("StoreConnector", self._store)
        set_key = self._key("dedup_keys" if kind is None else f"dedup_keys:{kind}")
        members = await connector.set_members(set_key)
        results = []
        from job_ftch.domain import RememberedDedupKey

        for member in sorted(members):
            raw = await connector.get(self._key(f"dedup_record:{member}"))
            if raw:
                results.append(RememberedDedupKey.model_validate_json(raw))
        return tuple(results)

    async def record_duplicate(self, record: Any) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set_add(self._key("dup_records"), record.item_id)
        await connector.set(self._key(f"dup_record:{record.item_id}"), record.model_dump_json())

    async def list_duplicate_records(self) -> tuple[Any, ...]:
        connector = cast("StoreConnector", self._store)
        members = await connector.set_members(self._key("dup_records"))
        results = []
        from job_ftch.domain import DuplicateRecord

        for member in sorted(members):
            raw = await connector.get(self._key(f"dup_record:{member}"))
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
        connector = cast("StoreConnector", self._store)
        value = await connector.get(
            self._run_state_key(key, source_kind=source_kind, source_name=source_name)
        )
        return None if value is None else str(value)

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set(
            self._run_state_key(key, source_kind=source_kind, source_name=source_name),
            value,
        )

    async def get_source_strategy(self, domain: str) -> dict[str, str] | None:
        return await self._store.get_source_strategy(f"{self._tenant_id}:{domain}")

    async def save_source_strategy(self, domain: str, monitor: str, bypass: str) -> None:
        await self._store.save_source_strategy(f"{self._tenant_id}:{domain}", monitor, bypass)

    def incremental_cursor(self) -> IncrementalCursor:
        return IncrementalCursor(cast("StoreConnector", self))

    async def save_run_summary(self, summary: RunSummary) -> None:
        connector = cast("StoreConnector", self._store)
        run_id = summary.source_run_id
        if not run_id:
            msg = "RunSummary.source_run_id is required to persist run history."
            raise ValueError(msg)
        raw = json.dumps(
            summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True
        )
        history_key = self._key(f"run_history:{run_id}")
        history_ids_key = self._key("run_history_ids")
        await connector.set(history_key, raw)
        try:
            await connector.set_add(history_ids_key, run_id)
        except Exception:
            with suppress(Exception):
                await connector.delete(history_key)
            logger.warning(
                "run_history_persist_rolled_back",
                tenant_id=self._tenant_id,
                run_id=run_id,
            )
            raise

    async def get_source_health(self, source_id: str) -> SourceHealth | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(_source_health_key(source_id)))
        if raw is None:
            return None
        try:
            return SourceHealth.model_validate_json(raw)
        except (ValueError, TypeError):
            return None

    async def save_source_health(self, source_id: str, health: SourceHealth) -> None:
        connector = cast("StoreConnector", self._store)
        health_key = self._key(_source_health_key(source_id))
        health_ids_key = self._key("source_health_ids")
        await connector.set(
            health_key,
            health.model_dump_json(),
        )
        try:
            await connector.set_add(health_ids_key, source_id)
        except Exception:
            with suppress(Exception):
                await connector.delete(health_key)
            logger.warning(
                "source_health_persist_rolled_back",
                tenant_id=self._tenant_id,
                source_id=source_id,
            )
            raise

    async def list_source_health(self) -> list[SourceHealth]:
        connector = cast("StoreConnector", self._store)
        source_ids = sorted(await connector.set_members(self._key("source_health_ids")))
        payloads: list[SourceHealth] = []
        for source_id in source_ids:
            payload = await self.get_source_health(source_id)
            if payload is not None:
                payloads.append(payload)
        return payloads

    async def get_runtime_source(self, source_id: str) -> RuntimeSourceRecord | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(_runtime_source_key(source_id)))
        if raw is None:
            return None
        try:
            return RuntimeSourceRecord.model_validate_json(raw)
        except (TypeError, ValueError):
            return None

    async def save_runtime_source(self, record: RuntimeSourceRecord) -> None:
        connector = cast("StoreConnector", self._store)
        record_key = self._key(_runtime_source_key(record.source_id))
        record_ids_key = self._key("runtime_source_ids")
        await connector.set(
            record_key,
            record.model_dump_json(),
        )
        try:
            await connector.set_add(record_ids_key, record.source_id)
        except Exception:
            with suppress(Exception):
                await connector.delete(record_key)
            logger.warning(
                "runtime_source_persist_rolled_back",
                tenant_id=self._tenant_id,
                source_id=record.source_id,
            )
            raise

    async def delete_runtime_source(self, source_id: str) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.delete(self._key(_runtime_source_key(source_id)))

    async def list_runtime_sources(self) -> list[RuntimeSourceRecord]:
        connector = cast("StoreConnector", self._store)
        source_ids = sorted(await connector.set_members(self._key("runtime_source_ids")))
        records: list[RuntimeSourceRecord] = []
        for source_id in source_ids:
            record = await self.get_runtime_source(source_id)
            if record is not None:
                records.append(record)
        return records

    async def clear_runtime_sources(self) -> None:
        connector = cast("StoreConnector", self._store)
        source_ids_key = self._key("runtime_source_ids")
        source_ids = sorted(await connector.set_members(source_ids_key))
        snapshot = {
            source_id: await connector.get(self._key(_runtime_source_key(source_id)))
            for source_id in source_ids
        }
        await connector.clear_set(source_ids_key)
        try:
            for source_id in source_ids:
                await connector.delete(self._key(_runtime_source_key(source_id)))
        except Exception:
            for source_id in source_ids:
                with suppress(Exception):
                    await connector.set_add(source_ids_key, source_id)
            for source_id, raw in snapshot.items():
                if raw is None:
                    continue
                with suppress(Exception):
                    await connector.set(self._key(_runtime_source_key(source_id)), raw)
            logger.warning(
                "runtime_sources_clear_rolled_back",
                tenant_id=self._tenant_id,
                source_count=len(source_ids),
            )
            raise

    async def set_source_disabled(self, source_id: str, disabled: bool) -> None:
        connector = cast("StoreConnector", self._store)
        key = self._key(_source_disabled_key(source_id))
        index_key = self._key("source_disabled_ids")
        if disabled:
            await connector.set(key, "1")
        else:
            await connector.delete(key)
        try:
            if disabled:
                await connector.set_add(index_key, source_id)
            else:
                members = sorted(await connector.set_members(index_key))
                await connector.clear_set(index_key)
                for member in members:
                    if member == source_id:
                        continue
                    if await connector.get(self._key(_source_disabled_key(member))) is None:
                        continue
                    await connector.set_add(index_key, member)
        except Exception:
            if disabled:
                with suppress(Exception):
                    await connector.delete(key)
                logger.warning(
                    "source_disabled_persist_rolled_back",
                    tenant_id=self._tenant_id,
                    source_id=source_id,
                )
                raise
            logger.warning(
                "source_disabled_index_write_failed",
                tenant_id=self._tenant_id,
                source_id=source_id,
            )

    async def list_disabled_source_ids(self) -> builtins.set[str]:
        connector = cast("StoreConnector", self._store)
        disabled_ids = sorted(await connector.set_members(self._key("source_disabled_ids")))
        result: builtins.set[str] = set()
        for source_id in disabled_ids:
            if await connector.get(self._key(_source_disabled_key(source_id))) is not None:
                result.add(source_id)
        return result

    async def get_candidate_profile(
        self,
        user_id: str,
        profile_id: str,
    ) -> ManagedCandidateProfile | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(_candidate_profile_key(user_id, profile_id)))
        if raw is None:
            return None
        try:
            return ManagedCandidateProfile.model_validate_json(raw)
        except (TypeError, ValueError) as exc:
            errors: list[Any] = getattr(exc, "errors", lambda: [])()
            logger.warning(
                "candidate_profile_load_failed",
                tenant_id=self._tenant_id,
                user_id=user_id,
                profile_id=profile_id,
                errors=[
                    {
                        "loc": error.get("loc"),
                        "type": error.get("type"),
                        "msg": error.get("msg"),
                    }
                    for error in errors[:5]
                    if isinstance(error, dict)
                ],
            )
            return None

    async def save_candidate_profile(self, record: ManagedCandidateProfile) -> None:
        connector = cast("StoreConnector", self._store)
        profile_key = self._key(_candidate_profile_key(record.user_id, record.profile_id))
        profile_ids_key = self._key(f"candidate_profile_ids:{record.user_id}")
        profile_user_ids_key = self._key("candidate_profile_user_ids")
        await connector.set(
            profile_key,
            record.model_dump_json(),
        )
        try:
            await connector.set_add(
                profile_ids_key,
                record.profile_id,
            )
            await connector.set_add(profile_user_ids_key, record.user_id)
        except Exception:
            with suppress(Exception):
                await connector.delete(profile_key)
            logger.warning(
                "candidate_profile_persist_rolled_back",
                tenant_id=self._tenant_id,
                user_id=record.user_id,
                profile_id=record.profile_id,
            )
            raise

    async def save_and_activate_candidate_profile(self, record: ManagedCandidateProfile) -> None:
        """Persist the profile AND mark it as the user's active profile.

        Replaces the previous two-call pattern
        ``save_candidate_profile(...); set_active_candidate_profile(...)``
        that the bot (and the eval harness) had to repeat at every
        example add. A pure two-call sequence is racy on the bot's
        first ``/run`` because the runner's
        ``has_candidate_profile_data`` reads ``get_active_candidate_profile_ids``
        right after the save — the second coroutine that flips the
        active bit hadn't yet been awaited, so the user got
        "Профиль не настроен" even though the profile was already
        on disk. The fix is to make the two writes part of the same
        method, document the contract, and have callers use it.

        Ordering: write the profile first, then the active marker.
        If the second call fails the profile is still recoverable on
        disk; the next ``/run`` will simply repeat the activation
        step. The previous ordering (active-first) lost the data on
        the same kind of partial failure.
        """
        await self.save_candidate_profile(record)
        await self.set_active_candidate_profile(record.user_id, record.profile_id)

    async def list_candidate_profiles(self, user_id: str) -> list[ManagedCandidateProfile]:
        connector = cast("StoreConnector", self._store)
        profile_ids = sorted(
            await connector.set_members(self._key(f"candidate_profile_ids:{user_id}"))
        )
        records: list[ManagedCandidateProfile] = []
        for profile_id in profile_ids:
            record = await self.get_candidate_profile(user_id, profile_id)
            if record is not None:
                records.append(record)
        return records

    async def set_active_candidate_profile(
        self,
        user_id: str,
        profile_id: str,
    ) -> None:
        connector = cast("StoreConnector", self._store)
        current_primary = await self.get_active_candidate_profile_id(user_id)
        if current_primary and current_primary != profile_id:
            await connector.set_add(
                self._key(_active_candidate_profiles_key(user_id)), current_primary
            )
        primary_key = self._key(_active_candidate_profile_key(user_id))
        active_set_key = self._key(_active_candidate_profiles_key(user_id))
        await connector.set(primary_key, profile_id)
        try:
            await connector.set_add(active_set_key, profile_id)
        except Exception:
            with suppress(Exception):
                if current_primary:
                    await connector.set(primary_key, current_primary)
                else:
                    await connector.delete(primary_key)
            logger.warning(
                "active_candidate_profile_rollback",
                tenant_id=self._tenant_id,
                user_id=user_id,
                profile_id=profile_id,
            )
            raise

    async def get_active_candidate_profile_id(self, user_id: str) -> str | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(_active_candidate_profile_key(user_id)))
        return None if raw is None else str(raw)

    async def get_active_candidate_profile_ids(self, user_id: str) -> tuple[str, ...]:
        connector = cast("StoreConnector", self._store)
        active_ids = sorted(
            await connector.set_members(self._key(_active_candidate_profiles_key(user_id)))
        )
        primary = await self.get_active_candidate_profile_id(user_id)
        ordered = [item for item in ([primary] if primary else []) + active_ids if item]
        seen: set[str] = set()
        result: list[str] = []
        for item in ordered:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return tuple(result)

    async def unset_active_candidate_profile(self, user_id: str, profile_id: str) -> None:
        connector = cast("StoreConnector", self._store)
        active_ids = await self.get_active_candidate_profile_ids(user_id)
        remaining = [item for item in active_ids if item != profile_id]
        active_set_key = self._key(_active_candidate_profiles_key(user_id))
        primary_key = self._key(_active_candidate_profile_key(user_id))
        primary = await self.get_active_candidate_profile_id(user_id)
        try:
            await connector.delete(active_set_key)
            for item in remaining:
                await connector.set_add(active_set_key, item)
            if primary == profile_id:
                if remaining:
                    await connector.set(primary_key, remaining[0])
                else:
                    await connector.delete(primary_key)
        except Exception:
            with suppress(Exception):
                await connector.delete(active_set_key)
                for item in active_ids:
                    await connector.set_add(active_set_key, item)
                if primary is not None:
                    await connector.set(primary_key, primary)
                else:
                    await connector.delete(primary_key)
            logger.warning(
                "active_candidate_profile_unset_rollback",
                tenant_id=self._tenant_id,
                user_id=user_id,
                profile_id=profile_id,
            )
            raise

    async def list_candidate_profile_users(self) -> tuple[str, ...]:
        connector = cast("StoreConnector", self._store)
        return tuple(sorted(await connector.set_members(self._key("candidate_profile_user_ids"))))

    async def list_all_active_candidate_profiles(self) -> list[ManagedCandidateProfile]:
        records: list[ManagedCandidateProfile] = []
        for user_id in await self.list_candidate_profile_users():
            for profile_id in await self.get_active_candidate_profile_ids(user_id):
                record = await self.get_candidate_profile(user_id, profile_id)
                if record is not None:
                    records.append(record)
        return records

    async def get_run_summary(self, run_id: str) -> RunSummary | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(f"run_history:{run_id}"))
        if raw is None:
            return None
        try:
            return _summary_from_payload(json.loads(raw), tenant_id=self._tenant_id)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "run_history_decode_failed",
                tenant_id=self._tenant_id,
                run_id=run_id,
                error=str(exc),
            )
            return None

    async def list_run_summaries(self, *, limit: int = 20) -> list[RunSummary]:
        connector = cast("StoreConnector", self._store)
        run_ids = await connector.set_members(self._key("run_history_ids"))
        summaries: list[RunSummary] = []
        for run_id in run_ids:
            summary = await self.get_run_summary(run_id)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=_summary_sort_key, reverse=True)
        return summaries[: max(limit, 0)]

    async def reset_namespace(self) -> None:
        reset = getattr(self._store, "reset_namespace", None)
        if not callable(reset):
            msg = f"Store backend {type(self._store).__name__} does not support namespace reset."
            raise NotImplementedError(msg)
        await reset(f"{self._tenant_id}:")

    async def clear_run_artifacts(self) -> dict[str, int]:
        clear = getattr(self._store, "clear_run_artifacts", None)
        if not callable(clear):
            msg = f"Store backend {type(self._store).__name__} cannot clear run artifacts."
            raise NotImplementedError(msg)
        result = await clear(f"{self._tenant_id}:", self._tenant_id)
        if not isinstance(result, dict):
            raise TypeError("Store clear_run_artifacts() must return a counter mapping.")
        return {str(key): int(value) for key, value in result.items()}

    async def close(self) -> None:
        close = getattr(self._store, "close", None)
        if callable(close):
            await close()
