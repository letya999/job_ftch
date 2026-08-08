"""In-memory store for tests and local runs."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from job_ftch.application.registry import register_store
from job_ftch.domain import (
    DuplicateRecord,
    ObservationLedgerEntry,
    OutboxRecord,
    OutboxState,
    RememberedDedupKey,
)

if TYPE_CHECKING:
    from job_ftch.application.contracts import DedupReservation
    from job_ftch.config import Settings
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
        self._source_assessments: dict[tuple[str, str], SourceAssessmentResult] = {}
        self._source_ingest_states: dict[tuple[str, str], SourceIngestState] = {}
        # ADR-031: per-tenant/source snapshot state.
        # Key: "snap_runs:{tenant}\x00{source}" → OrderedDict[run_id, (run_seq, {stable_id: item_hash})]
        self._snapshot_runs: dict[str, OrderedDict[str, tuple[int, dict[str, str]]]] = {}
        self._snapshot_seq: dict[str, int] = {}
        self._dedup_claims: dict[str, tuple[str, datetime]] = {}
        self._outbox: dict[str, OutboxRecord] = {}

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

    async def clear_run_artifacts(self, prefix: str, tenant_id: str) -> dict[str, int]:
        """Remove run-produced state while preserving tenant configuration and profiles."""
        suffixes = (
            "relevance:",
            "presentable:",
            "processed_at:",
            "dedup_record:",
            "dup_record:",
            "enrichment:",
            "resolver:",
            "pipeline.",
            "snapshot:",
            "source_health:",
            "bot_publish:",
            "bot_scheduler:last_publish",
            "bot_scheduler:pending_publish_since",
            "outcome:",
            "outcome_ids:",
            "outcome_run_order:",
        )
        kv_keys = [
            key
            for key in self._kv
            if any(key.startswith(f"{prefix}{suffix}") for suffix in suffixes)
        ]
        set_keys = [
            key
            for key in self._sets
            if any(
                key.startswith(f"{prefix}{suffix}")
                for suffix in (
                    "processed",
                    "dedup_keys",
                    "dup_records",
                    "source_health_ids",
                    "outcome_ids:",
                )
            )
        ]
        observation_prefix = f"observation:{tenant_id}:"
        observation_keys = [key for key in self._kv if key.startswith(observation_prefix)]
        snapshot_keys = [key for key in self._snapshot_runs if f"snap_runs:{tenant_id}\x00" in key]
        ingest_keys = [key for key in self._source_ingest_states if key[0] == tenant_id]
        dedup_claims = [key for key in self._dedup_claims if key.startswith(prefix)]
        outbox_keys = [key for key, record in self._outbox.items() if record.tenant_id == tenant_id]
        counts = {
            "kv": len(kv_keys),
            "sets": len(set_keys),
            "observations": len(observation_keys),
            "snapshots": len(snapshot_keys),
            "source_ingest_states": len(ingest_keys),
            "dedup_claims": len(dedup_claims),
            "outbox": len(outbox_keys),
        }
        for key in (*kv_keys, *observation_keys):
            self._kv.pop(key, None)
        for key in set_keys:
            self._sets.pop(key, None)
        for snapshot_key in snapshot_keys:
            self._snapshot_runs.pop(snapshot_key, None)
            self._snapshot_seq.pop(snapshot_key, None)
        for ingest_key in ingest_keys:
            self._source_ingest_states.pop(ingest_key, None)
        for claim_key in dedup_claims:
            self._dedup_claims.pop(claim_key, None)
        for outbox_key in outbox_keys:
            self._outbox.pop(outbox_key, None)
        return counts

    # Store methods — built on top of StoreConnector primitives

    async def enqueue_outbox(self, record: OutboxRecord) -> OutboxRecord:
        return self._outbox.setdefault(
            record.idempotency_key, record.model_copy(update={"state": OutboxState.OUTBOXED})
        )

    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[OutboxRecord, ...]:
        return tuple(
            record
            for record in self._outbox.values()
            if record.state is OutboxState.OUTBOXED
            and (tenant_id is None or record.tenant_id == tenant_id)
        )[:limit]

    async def mark_outbox_delivered(self, idempotency_key: str) -> OutboxRecord | None:
        record = self._outbox.get(idempotency_key)
        if record is None:
            return None
        delivered = record.model_copy(update={"state": OutboxState.DELIVERED})
        self._outbox[idempotency_key] = delivered
        return delivered

    async def acquire_dedup_claim(self, key: str, owner_id: str, *, ttl_seconds: int) -> bool:
        owner, expiry = self._dedup_claims.get(key, ("", datetime.min.replace(tzinfo=UTC)))
        if owner and expiry > datetime.now(UTC) and owner != owner_id:
            return False
        self._dedup_claims[key] = (owner_id, datetime.now(UTC) + timedelta(seconds=ttl_seconds))
        return True

    async def release_dedup_claim(self, key: str, owner_id: str) -> None:
        if self._dedup_claims.get(key, (None, None))[0] == owner_id:
            self._dedup_claims.pop(key, None)

    async def compare_and_reserve(
        self, keys: tuple[str, ...], owner_id: str, *, ttl_seconds: int
    ) -> DedupReservation:
        from job_ftch.application.contracts import DedupReservation

        now = datetime.now(UTC)
        for key in keys:
            owner, expiry = self._dedup_claims.get(key, ("", datetime.min.replace(tzinfo=UTC)))
            if owner and expiry > now and owner != owner_id:
                return DedupReservation(acquired=False, conflicting_key=key)
        expiry_at = now + timedelta(seconds=ttl_seconds)
        for key in keys:
            self._dedup_claims[key] = (owner_id, expiry_at)
        return DedupReservation(acquired=True, reserved_keys=keys)

    async def record_observation(self, entry: ObservationLedgerEntry) -> ObservationLedgerEntry:
        key = f"observation:{entry.tenant_id}:{entry.stable_id}:{entry.content_hash}"
        existing = await self.get_observation(
            entry.stable_id, entry.content_hash, tenant_id=entry.tenant_id
        )
        if existing is not None:
            return existing
        versions = await self.set_members(
            f"observation_versions:{entry.tenant_id}:{entry.stable_id}"
        )
        recorded = entry.model_copy(update={"content_version": len(versions) + 1})
        await self.set(key, recorded.model_dump_json())
        await self.set_add(
            f"observation_versions:{entry.tenant_id}:{entry.stable_id}", entry.content_hash
        )
        return recorded

    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> ObservationLedgerEntry | None:
        raw = await self.get(f"observation:{tenant_id}:{stable_id}:{content_hash}")
        return ObservationLedgerEntry.model_validate_json(raw) if raw else None

    async def has_processed(self, item_id: str) -> bool:
        from job_ftch.config import get_settings

        ttl_hours = get_settings().processed_item_ttl_hours
        timestamp = await self.get(_processed_timestamp_key(item_id))
        if timestamp is not None:
            return _is_processed_timestamp_fresh(timestamp, ttl_hours)
        return await self.set_contains("processed", item_id)

    async def mark_processed(self, item_id: str) -> None:
        await self.set_add("processed", item_id)
        await self.set(_processed_timestamp_key(item_id), datetime.now(UTC).isoformat())

    async def has_dedup_key(self, key: str) -> bool:
        return await self.set_contains("dedup_keys", key)

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        await self.set_add("dedup_keys", record.key)
        await self.set_add(f"dedup_keys:{record.kind.value}", record.key)
        await self.set(f"dedup_record:{record.key}", record.model_dump_json())

    async def get_dedup_key(self, key: str) -> RememberedDedupKey | None:
        raw = await self.get(f"dedup_record:{key}")
        if raw is None:
            return None
        return RememberedDedupKey.model_validate_json(raw)

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

    # ADR-031: source snapshot methods (in-memory backed)
    #
    # State is keyed by (tenant_id, source_id) → list of (run_id, run_seq, stable_id, item_hash).
    # `run_seq` is a monotonic counter per (tenant, source) used to find the last run
    # deterministically without timestamps.

    _SNAPSHOT_KEY_SEP = "\x00"
    _SNAPSHOT_TTL_DAYS = 7  # default TTL for in-memory mode (matches backend default)

    def _snapshot_state(
        self,
        tenant_id: str,
        source_id: str,
    ) -> OrderedDict[str, tuple[int, dict[str, str]]]:
        """Return {run_id: (run_seq, {stable_id: item_hash})} for this (tenant, source)."""
        key = f"snap_runs:{tenant_id}{self._SNAPSHOT_KEY_SEP}{source_id}"
        return self._snapshot_runs.setdefault(key, OrderedDict())

    def _snapshot_last_run_id(
        self,
        tenant_id: str,
        source_id: str,
    ) -> str | None:
        state = self._snapshot_state(tenant_id, source_id)
        if not state:
            return None
        return next(reversed(state))

    def _snapshot_run_seq(
        self,
        tenant_id: str,
        source_id: str,
    ) -> int:
        seq_key = f"{tenant_id}{self._SNAPSHOT_KEY_SEP}{source_id}"
        return self._snapshot_seq.get(seq_key, 0)

    async def get_last_run_snapshot(
        self,
        tenant_id: str,
        source_id: str,
    ) -> frozenset[str]:
        last_run = self._snapshot_last_run_id(tenant_id, source_id)
        if last_run is None:
            return frozenset()
        state = self._snapshot_state(tenant_id, source_id)
        _, items = state[last_run]
        return frozenset(items)

    async def get_last_run_snapshot_hashes(self, tenant_id: str, source_id: str) -> dict[str, str]:
        last_run = self._snapshot_last_run_id(tenant_id, source_id)
        if last_run is None:
            return {}
        _, items = self._snapshot_state(tenant_id, source_id)[last_run]
        return dict(items)

    async def save_snapshot_rows(
        self,
        tenant_id: str,
        source_id: str,
        run_id: str,
        rows: tuple[tuple[str, str, str], ...],
    ) -> None:
        if not rows:
            return
        state = self._snapshot_state(tenant_id, source_id)
        seq_key = f"{tenant_id}{self._SNAPSHOT_KEY_SEP}{source_id}"
        next_seq = self._snapshot_seq.get(seq_key, 0) + 1
        self._snapshot_seq[seq_key] = next_seq
        state[run_id] = (next_seq, {sid: h for sid, h, _ in rows})
        state.move_to_end(run_id)

    async def purge_old_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        *,
        older_than_days: int,
    ) -> int:
        """In-memory purge: drop the oldest runs beyond a window proportional to ttl.

        In-memory mode has no wall clock; we approximate by retaining the last
        max(1, older_than_days) runs per (tenant, source). 1-day TTL → 1 run.
        """
        if older_than_days <= 0:
            return 0
        state = self._snapshot_state(tenant_id, source_id)
        if len(state) <= older_than_days:
            return 0
        runs_to_drop = len(state) - max(1, older_than_days)
        deleted = 0
        for run_id in list(state.keys())[:runs_to_drop]:
            del state[run_id]
            deleted += 1
        return deleted

    async def get_source_assessment(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceAssessmentResult | None:
        return self._source_assessments.get((tenant_id, source_id))

    async def save_source_assessment(
        self,
        tenant_id: str,
        result: SourceAssessmentResult,
    ) -> None:
        self._source_assessments[(tenant_id, result.source_id)] = result

    async def get_source_ingest_state(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceIngestState | None:
        return self._source_ingest_states.get((tenant_id, source_id))

    async def save_source_ingest_state(
        self,
        tenant_id: str,
        state: SourceIngestState,
    ) -> None:
        self._source_ingest_states[(tenant_id, state.source_id)] = state


@register_store("memory")
def _build_in_memory_store(settings: Settings) -> InMemoryStore:
    return InMemoryStore(
        max_keys=settings.memory_max_keys,
        max_set_members=settings.memory_max_set_members,
    )
