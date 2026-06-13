"""Multi-tenant pipeline orchestration and tenant-scoped service helpers."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.builder import (
    PipelineBuilder,
    build_nodes,
    build_output_sinks,
    build_quarantine_sink,
    build_rejected_sink,
    load_profile_catalog,
    tenant_to_settings,
)
from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.registry import (
    create_job_backend,
    create_job_group_store,
    create_llm,
    create_search_backend,
    create_store,
)
from job_ftch.application.watermark import IncrementalCursor
from job_ftch.config import get_settings
from job_ftch.domain import (
    JobGroup,
    JobLineage,
    JobRecord,
    ManagedCandidateProfile,
    RuntimeSourceRecord,
    SourceHealth,
    TenantConfig,
    TenantInfo,
    build_job_lineage,
    source_spec_identifier,
    source_spec_locator,
    source_spec_name,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.application.contracts import (
        AuthProvider,
        JobGroupStore,
        JobPersistenceBackend,
        LLMProvider,
        MetricsExporter,
        SearchBackend,
        Store,
        StoreConnector,
    )
    from job_ftch.config import Settings
    from job_ftch.domain.source_spec import SourceSpec

logger = structlog.get_logger(__name__)
_PROCESS_TENANT_LOCKS: dict[str, asyncio.Lock] = {}


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    return str(value)


def _summary_sort_key(summary: RunSummary) -> tuple[str, str]:
    finished = summary.finished_at
    started = summary.started_at
    finished_key = finished.isoformat() if isinstance(finished, datetime) else str(finished or "")
    started_key = started.isoformat() if isinstance(started, datetime) else str(started or "")
    return (finished_key, started_key)


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


def _source_health_key(source_id: str) -> str:
    return f"source_health:{source_id}"


def _runtime_source_key(source_id: str) -> str:
    return f"runtime_source:{source_id}"


def _source_disabled_key(source_id: str) -> str:
    return f"source_disabled:{source_id}"


def _candidate_profile_key(user_id: str, profile_id: str) -> str:
    return f"candidate_profile:{user_id}:{profile_id}"


def _active_candidate_profile_key(user_id: str) -> str:
    return f"candidate_profile_active:{user_id}"


def _update_source_health_payload(
    previous: SourceHealth | None,
    *,
    source_id: str,
    source_kind: str,
    source_name: str,
    stats: SourceRunStats,
    finished_at: datetime,
    drift_ratio_threshold: float = 0.2,
    min_baseline_threshold: float = 3.0,
    failure_streak_pause: int = 3,
) -> SourceHealth:
    prev_baseline_emitted = previous.baseline_emitted if previous else 0.0
    previous_success = previous.success_count if previous else 0
    current_emitted = int(stats.emitted)
    had_failure = stats.failed > 0
    degraded = False
    drift_ratio: float | None = None
    if prev_baseline_emitted >= min_baseline_threshold:
        drift_ratio = current_emitted / prev_baseline_emitted if prev_baseline_emitted > 0 else 0.0
        degraded = current_emitted == 0 or drift_ratio < drift_ratio_threshold

    if had_failure:
        failure_streak = (previous.failure_streak if previous else 0) + 1
        last_success_at = previous.last_success_at if previous else None
        success_count = previous_success
        next_baseline = prev_baseline_emitted
        if failure_streak >= failure_streak_pause and (not previous or not previous.paused):
            logger.info("source_auto_paused", source_id=source_id, failure_streak=failure_streak)
    else:
        failure_streak = 0
        last_success_at = finished_at.isoformat()
        success_count = previous_success + 1
        next_baseline = (
            float(current_emitted)
            if success_count == 1
            else round((prev_baseline_emitted * 0.7) + (current_emitted * 0.3), 4)
        )

    return SourceHealth(
        source_id=source_id,
        source_kind=source_kind,
        source_name=source_name,
        last_run_at=finished_at.isoformat(),
        last_success_at=last_success_at,
        failure_streak=failure_streak,
        success_count=success_count,
        last_fetched=stats.fetched,
        last_emitted=current_emitted,
        last_failed=stats.failed,
        last_quarantined=stats.quarantined,
        baseline_emitted=next_baseline,
        drift_ratio=drift_ratio,
        degraded=degraded,
        status="degraded"
        if degraded
        else ("paused" if failure_streak >= failure_streak_pause else "healthy"),
        paused=failure_streak >= failure_streak_pause,
        skipped_runs=0 if not had_failure else (previous.skipped_runs if previous else 0),
    )


def _build_source_listing_payload(
    spec: SourceSpec,
    *,
    origin: str,
    enabled: bool,
    health: SourceHealth | None,
) -> dict[str, Any]:
    source_id = source_spec_identifier(spec)
    payload: dict[str, Any] = {
        "source_id": source_id,
        "source_kind": spec.type,
        "source_name": source_spec_name(spec),
        "locator": source_spec_locator(spec),
        "origin": origin,
        "enabled": enabled,
        "spec": spec.model_dump(mode="json"),
    }
    if health is None:
        payload.update(
            {
                "status": "disabled" if not enabled else "pending",
                "failure_streak": 0,
                "last_emitted": 0,
                "last_failed": 0,
                "last_quarantined": 0,
                "degraded": False,
            }
        )
        return payload
    payload.update(health.model_dump(mode="json"))
    if not enabled:
        payload["status"] = "disabled"
    return payload


def _merge_effective_sources(
    base_sources: tuple[SourceSpec, ...],
    runtime_sources: dict[str, RuntimeSourceRecord],
    disabled_source_ids: set[str],
) -> list[SourceSpec]:
    merged: list[SourceSpec] = []
    seen: set[str] = set()
    for spec in base_sources:
        source_id = source_spec_identifier(spec)
        seen.add(source_id)
        if source_id not in disabled_source_ids:
            merged.append(spec)
    for source_id, record in runtime_sources.items():
        if source_id in seen:
            continue
        if record.enabled and source_id not in disabled_source_ids:
            merged.append(record.spec)
    return merged


class TenantStore:
    """Store wrapper that prefixes every key space with a tenant slug."""

    def __init__(self, tenant_id: str, store: Store) -> None:
        self._tenant_id = tenant_id
        self._store = store

    def _key(self, key: str) -> str:
        return f"{self._tenant_id}:{key}"

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

    async def has_processed(self, item_id: str) -> bool:
        connector = cast("StoreConnector", self._store)
        return bool(await connector.set_contains(self._key("processed"), item_id))

    async def mark_processed(self, item_id: str) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set_add(self._key("processed"), item_id)

    async def has_dedup_key(self, key: str) -> bool:
        connector = cast("StoreConnector", self._store)
        return bool(await connector.set_contains(self._key("dedup_keys"), key))

    async def remember_dedup_key(self, record: Any) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set_add(self._key("dedup_keys"), record.key)
        await connector.set_add(self._key(f"dedup_keys:{record.kind.value}"), record.key)
        await connector.set(self._key(f"dedup_record:{record.key}"), record.model_dump_json())

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
        return await self._store.get_source_strategy(domain)

    async def save_source_strategy(self, domain: str, monitor: str, bypass: str) -> None:
        await self._store.save_source_strategy(domain, monitor, bypass)

    def incremental_cursor(self) -> IncrementalCursor:
        connector = cast("StoreConnector", self._store)
        return IncrementalCursor(connector)

    async def save_run_summary(self, summary: RunSummary) -> None:
        connector = cast("StoreConnector", self._store)
        run_id = summary.source_run_id
        if not run_id:
            msg = "RunSummary.source_run_id is required to persist run history."
            raise ValueError(msg)
        raw = json.dumps(
            summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True
        )
        await connector.set(self._key(f"run_history:{run_id}"), raw)
        await connector.set_add(self._key("run_history_ids"), run_id)

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
        await connector.set(
            self._key(_source_health_key(source_id)),
            health.model_dump_json(),
        )
        await connector.set_add(self._key("source_health_ids"), source_id)

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
        await connector.set(
            self._key(_runtime_source_key(record.source_id)),
            record.model_dump_json(),
        )
        await connector.set_add(self._key("runtime_source_ids"), record.source_id)

    async def list_runtime_sources(self) -> list[RuntimeSourceRecord]:
        connector = cast("StoreConnector", self._store)
        source_ids = sorted(await connector.set_members(self._key("runtime_source_ids")))
        records: list[RuntimeSourceRecord] = []
        for source_id in source_ids:
            record = await self.get_runtime_source(source_id)
            if record is not None:
                records.append(record)
        return records

    async def set_source_disabled(self, source_id: str, disabled: bool) -> None:
        connector = cast("StoreConnector", self._store)
        key = self._key(_source_disabled_key(source_id))
        if disabled:
            await connector.set(key, "1")
        else:
            await connector.delete(key)
        await connector.set_add(self._key("source_disabled_ids"), source_id)

    async def list_disabled_source_ids(self) -> set[str]:
        connector = cast("StoreConnector", self._store)
        disabled_ids = sorted(await connector.set_members(self._key("source_disabled_ids")))
        result: set[str] = set()
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
        except (TypeError, ValueError):
            return None

    async def save_candidate_profile(self, record: ManagedCandidateProfile) -> None:
        connector = cast("StoreConnector", self._store)
        await connector.set(
            self._key(_candidate_profile_key(record.user_id, record.profile_id)),
            record.model_dump_json(),
        )
        await connector.set_add(
            self._key(f"candidate_profile_ids:{record.user_id}"),
            record.profile_id,
        )

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
        await connector.set(self._key(_active_candidate_profile_key(user_id)), profile_id)

    async def get_active_candidate_profile_id(self, user_id: str) -> str | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(_active_candidate_profile_key(user_id)))
        return None if raw is None else str(raw)

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

    async def close(self) -> None:
        close = getattr(self._store, "close", None)
        if callable(close):
            await close()


@dataclass
class TenantRuntime:
    tenant: TenantConfig
    settings: Settings
    auth_provider: AuthProvider
    store: TenantStore
    builder: PipelineBuilder
    job_group_store: JobGroupStore
    search_backend: SearchBackend
    job_backend: JobPersistenceBackend
    metrics_exporter: MetricsExporter | None = None
    base_sources: tuple[SourceSpec, ...] = field(default_factory=tuple)
    runtime_sources: dict[str, RuntimeSourceRecord] = field(default_factory=dict)
    disabled_source_ids: set[str] = field(default_factory=set)
    sources_loaded: bool = False


class TenantRunner:
    def __init__(self, runtimes: dict[str, TenantRuntime]) -> None:
        self._runtimes = runtimes

    @classmethod
    def from_tenants(
        cls,
        tenants: Sequence[TenantConfig],
        *,
        base_settings: Settings | None = None,
    ) -> TenantRunner:
        settings_template = base_settings or get_settings()
        runtimes: dict[str, TenantRuntime] = {}
        metrics_exporters_by_port: dict[int, MetricsExporter] = {}
        for tenant in tenants:
            tenant_settings = tenant_to_settings(tenant, settings_template)
            metrics_exporter: MetricsExporter | None = None
            if tenant_settings.metrics_enabled:
                metrics_exporter = metrics_exporters_by_port.get(tenant_settings.metrics_port)
                if metrics_exporter is None:
                    from job_ftch.infrastructure.metrics.prometheus import (
                        PrometheusExporter,
                    )

                    metrics_exporter = PrometheusExporter(
                        start_server=True,
                        port=tenant_settings.metrics_port,
                    )
                    metrics_exporters_by_port[tenant_settings.metrics_port] = metrics_exporter
            auth = resolve_auth_provider(tenant.auth_provider, settings=tenant_settings)
            base_store = cast("Store", create_store(tenant_settings))
            tenant_store = TenantStore(tenant.tenant_id, base_store)
            job_group_store = cast("JobGroupStore", create_job_group_store(tenant_settings))
            llm = cast("LLMProvider", create_llm(tenant_settings))
            catalog = load_profile_catalog(tenant_settings)
            sanitize_node, nodes = build_nodes(
                tenant_settings,
                tenant_store,
                llm,
                job_group_store,
                catalog=catalog,
            )
            output_sink, review_sink, posting_sink = build_output_sinks(tenant_settings)
            rejected_counted, rejected_sink = build_rejected_sink(tenant_settings)
            builder = PipelineBuilder()
            builder.sources(tenant.sources)
            builder.auth(auth)
            builder.store(tenant_store)
            builder.stage(sanitize_node)
            for node in nodes:
                builder.stage(node)
            builder.sink(output_sink)
            builder.with_quarantine_sink(build_quarantine_sink(tenant_settings))
            builder.with_rejected_sink(rejected_sink, counted=rejected_counted)
            builder.set_default_max_items(tenant_settings.pipeline_max_items_per_run)
            builder.set_summary_context(
                review_sink=review_sink,
                posting_sink=posting_sink,
                job_group_store=job_group_store,
                profile_name=catalog.catalog_name,
                output_path=tenant_settings.output_path,
            )
            if tenant.schedule and tenant.schedule.interval_seconds is not None:
                builder.schedule(tenant.schedule.interval_seconds)
            runtimes[tenant.tenant_id] = TenantRuntime(
                tenant=tenant,
                settings=tenant_settings,
                auth_provider=auth,
                store=tenant_store,
                builder=builder,
                job_group_store=job_group_store,
                search_backend=cast("SearchBackend", create_search_backend(tenant_settings)),
                job_backend=cast("JobPersistenceBackend", create_job_backend(tenant_settings)),
                metrics_exporter=metrics_exporter,
                base_sources=tuple(tenant.sources),
            )
        return cls(runtimes)

    def tenant_ids(self) -> list[str]:
        return sorted(self._runtimes)

    def get_runtime(self, tenant_id: str) -> TenantRuntime:
        runtime = self._runtimes.get(tenant_id)
        if runtime is None:
            msg = f"Unknown tenant_id: {tenant_id}"
            raise KeyError(msg)
        return runtime

    async def _ensure_runtime_sources_loaded(self, runtime: TenantRuntime) -> None:
        if runtime.sources_loaded:
            return
        runtime.runtime_sources = {
            record.source_id: record for record in await runtime.store.list_runtime_sources()
        }
        runtime.disabled_source_ids = await runtime.store.list_disabled_source_ids()
        self._apply_runtime_sources(runtime)
        runtime.sources_loaded = True

    def _apply_runtime_sources(self, runtime: TenantRuntime) -> None:
        effective_sources = _merge_effective_sources(
            runtime.base_sources,
            runtime.runtime_sources,
            runtime.disabled_source_ids,
        )
        runtime.builder.sources(effective_sources)
        runtime.tenant = runtime.tenant.model_copy(update={"sources": effective_sources})

    async def add_source_spec(
        self,
        tenant_id: str,
        spec: SourceSpec,
        *,
        added_via: str = "runtime",
        added_by: str | None = None,
        input_value: str | None = None,
    ) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        source_id = source_spec_identifier(spec)
        base_ids = {source_spec_identifier(item) for item in runtime.base_sources}
        existing_runtime = runtime.runtime_sources.get(source_id)

        if source_id in base_ids and source_id not in runtime.disabled_source_ids:
            msg = f"Source already configured: {source_id}"
            raise ValueError(msg)
        if (
            existing_runtime is not None
            and existing_runtime.enabled
            and source_id not in runtime.disabled_source_ids
        ):
            msg = f"Source already configured: {source_id}"
            raise ValueError(msg)

        if source_id in base_ids:
            await runtime.store.set_source_disabled(source_id, False)
            runtime.disabled_source_ids.discard(source_id)
        else:
            record = RuntimeSourceRecord(
                source_id=source_id,
                spec=spec,
                enabled=True,
                added_via=added_via,
                added_by=added_by,
                input_value=input_value,
            )
            runtime.runtime_sources[source_id] = record
            await runtime.store.save_runtime_source(record)
            await runtime.store.set_source_disabled(source_id, False)
            runtime.disabled_source_ids.discard(source_id)

        self._apply_runtime_sources(runtime)
        payloads = await self.list_sources(tenant_id)
        for payload in payloads:
            if payload["source_id"] == source_id:
                return payload
        msg = f"Failed to activate source: {source_id}"
        raise RuntimeError(msg)

    async def disable_source(self, tenant_id: str, source_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        base_ids = {source_spec_identifier(item) for item in runtime.base_sources}
        runtime_record = runtime.runtime_sources.get(source_id)
        if source_id not in base_ids and runtime_record is None:
            msg = f"Unknown source_id: {source_id}"
            raise KeyError(msg)

        if runtime_record is not None:
            runtime_record = runtime_record.model_copy(update={"enabled": False})
            runtime.runtime_sources[source_id] = runtime_record
            await runtime.store.save_runtime_source(runtime_record)

        runtime.disabled_source_ids.add(source_id)
        await runtime.store.set_source_disabled(source_id, True)
        self._apply_runtime_sources(runtime)
        payloads = await self.list_sources(tenant_id)
        for payload in payloads:
            if payload["source_id"] == source_id:
                return payload
        msg = f"Failed to disable source: {source_id}"
        raise RuntimeError(msg)

    async def run_tenant(self, tenant_id: str, *, max_items: int | None = None) -> RunSummary:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)

        # Apply Task 3 (Rate Limit), Task 4 (Jitter), Task 5 (Auto-pause)
        effective_sources: list[SourceSpec] = []
        now = datetime.now(UTC)

        # Load health for all potential sources to evaluate runnability
        base_ids = {source_spec_identifier(s) for s in runtime.base_sources}
        runtime_ids = set(runtime.runtime_sources.keys())
        all_ids = base_ids | runtime_ids

        health_map: dict[str, SourceHealth] = {}
        for sid in all_ids:
            health = await runtime.store.get_source_health(sid)
            if health:
                health_map[sid] = health

        all_specs = list(runtime.base_sources) + [
            r.spec for r in runtime.runtime_sources.values() if r.enabled
        ]
        # Filter for unique specs by ID (handling overlaps between base and runtime)
        seen_specs: set[str] = set()
        unique_specs: list[SourceSpec] = []
        for spec in all_specs:
            sid = source_spec_identifier(spec)
            if sid in seen_specs or sid in runtime.disabled_source_ids:
                continue
            seen_specs.add(sid)
            unique_specs.append(spec)

        for spec in unique_specs:
            sid = source_spec_identifier(spec)
            health = health_map.get(sid)

            # Task 3: Rate Limit
            if health and health.last_run_at:
                try:
                    last_run = datetime.fromisoformat(health.last_run_at)
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=UTC)
                    wait_time = (now - last_run).total_seconds()
                    if wait_time < spec.rate_limit_min_interval_seconds:
                        logger.info(
                            "source_rate_limited",
                            source_id=sid,
                            wait_remaining=spec.rate_limit_min_interval_seconds - wait_time,
                        )
                        continue
                except (ValueError, TypeError):
                    pass

            # Task 5: Auto-pause with Half-open
            if health and health.paused:
                health.skipped_runs += 1
                if health.skipped_runs >= runtime.settings.source_health_probe_every_n_runs:
                    logger.info("source_probe", source_id=sid, skipped_runs=health.skipped_runs)
                    health.skipped_runs = 0
                else:
                    await runtime.store.save_source_health(sid, health)
                    continue
                # Save the updated skipped_runs before probing
                await runtime.store.save_source_health(sid, health)

            effective_sources.append(spec)

        if not effective_sources:
            # Return an empty summary if no sources are runnable this cycle
            summary = RunSummary()
            summary.tenant_id = tenant_id
            return summary

        # Task 4: Jitter before dispatching
        if runtime.settings.scheduler_jitter_seconds > 0:
            jitter = random.uniform(0.0, runtime.settings.scheduler_jitter_seconds)
            await asyncio.sleep(jitter)

        async with _tenant_run_lock(runtime.settings, tenant_id):
            builder = runtime.builder.clone()
            builder.sources(effective_sources)
            summary = await builder.run_async(max_items=max_items)

        summary.tenant_id = tenant_id
        await runtime.store.set_run_state(
            "pipeline.run_summary",
            json.dumps(
                summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True
            ),
        )
        await runtime.store.save_run_summary(summary)
        await self._update_source_health(runtime, summary)
        if runtime.metrics_exporter is not None:
            await runtime.metrics_exporter.observe_run(
                summary,
                tenant_id=tenant_id,
                job_group_total=await runtime.job_group_store.count(),
            )
        return summary

    async def _update_source_health(self, runtime: TenantRuntime, summary: RunSummary) -> None:
        finished_at = summary.finished_at or datetime.now()
        for source_id, stats in summary.by_source_id.items():
            source_kind, _, source_name = source_id.partition(":")
            previous = await runtime.store.get_source_health(source_id)
            payload = _update_source_health_payload(
                previous,
                source_id=source_id,
                source_kind=source_kind,
                source_name=source_name,
                stats=stats,
                finished_at=finished_at,
                drift_ratio_threshold=runtime.settings.source_health_drift_ratio,
                min_baseline_threshold=runtime.settings.source_health_min_baseline,
                failure_streak_pause=runtime.settings.source_health_failure_streak_pause,
            )
            await runtime.store.save_source_health(source_id, payload)
            if payload.degraded:
                logger.warning(
                    "source_drift_detected",
                    tenant_id=runtime.tenant.tenant_id,
                    source_id=source_id,
                    baseline_emitted=payload.baseline_emitted,
                    current_emitted=payload.last_emitted,
                    drift_ratio=payload.drift_ratio,
                )

    async def run_all(self, *, concurrency: int = 4) -> list[RunSummary]:
        semaphore = asyncio.Semaphore(max(concurrency, 1))

        async def run_one(tenant_id: str) -> RunSummary | None:
            async with semaphore:
                try:
                    return await self.run_tenant(tenant_id)
                except Exception as exc:
                    logger.error(
                        "tenant_run_failed",
                        tenant_id=tenant_id,
                        error=str(exc),
                        exc_info=True,
                    )
                    return None

        results = await asyncio.gather(*(run_one(tid) for tid in self.tenant_ids()))
        return [summary for summary in results if summary is not None]

    async def get_status(self, tenant_id: str) -> RunSummary | None:
        runtime = self.get_runtime(tenant_id)
        raw = await runtime.store.get_run_state("pipeline.run_summary")
        if raw is None:
            return None
        try:
            return _summary_from_payload(json.loads(raw), tenant_id=tenant_id)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("status_decode_failed", tenant_id=tenant_id, error=str(exc))
            return None

    async def list_tenants(self) -> list[TenantInfo]:
        result: list[TenantInfo] = []
        for tenant_id in self.tenant_ids():
            runtime = self.get_runtime(tenant_id)
            await self._ensure_runtime_sources_loaded(runtime)
            summary = await self.get_status(tenant_id)
            result.append(
                TenantInfo(
                    tenant_id=tenant_id,
                    display_name=runtime.tenant.display_name,
                    source_count=len(runtime.tenant.sources),
                    last_run=summary.as_dict() if summary is not None else None,
                )
            )
        return result

    async def list_source_health(self, tenant_id: str) -> list[dict[str, Any]]:
        health_models = await self.get_runtime(tenant_id).store.list_source_health()
        return [h.model_dump(mode="json") for h in health_models]

    async def list_sources(self, tenant_id: str) -> list[dict[str, Any]]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        health_models = await runtime.store.list_source_health()
        health_by_id = {h.source_id: h for h in health_models}
        health_by_name = {h.source_name: h for h in health_models}
        payloads: list[dict[str, Any]] = []
        for spec in runtime.base_sources:
            source_id = source_spec_identifier(spec)
            source_name = source_spec_name(spec)
            payloads.append(
                _build_source_listing_payload(
                    spec,
                    origin="config",
                    enabled=source_id not in runtime.disabled_source_ids,
                    health=health_by_id.get(source_id) or health_by_name.get(source_name),
                )
            )
        for source_id in sorted(runtime.runtime_sources):
            record = runtime.runtime_sources[source_id]
            source_name = source_spec_name(record.spec)
            payloads.append(
                _build_source_listing_payload(
                    record.spec,
                    origin=record.origin,
                    enabled=record.enabled and source_id not in runtime.disabled_source_ids,
                    health=health_by_id.get(source_id) or health_by_name.get(source_name),
                )
            )
        payloads.sort(key=lambda item: str(item["source_id"]))
        return payloads

    async def save_candidate_profile(
        self,
        tenant_id: str,
        record: ManagedCandidateProfile,
    ) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await runtime.store.save_candidate_profile(record)
        profiles = await self.list_candidate_profiles(tenant_id, record.user_id)
        for profile in profiles:
            if profile["profile_id"] == record.profile_id:
                return profile
        msg = f"Failed to persist candidate profile: {record.profile_id}"
        raise RuntimeError(msg)

    async def list_candidate_profiles(
        self,
        tenant_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        runtime = self.get_runtime(tenant_id)
        records = await runtime.store.list_candidate_profiles(user_id)
        active_profile_id = await runtime.store.get_active_candidate_profile_id(user_id)
        payloads: list[dict[str, Any]] = []
        for record in records:
            payloads.append(
                {
                    "user_id": record.user_id,
                    "profile_id": record.profile_id,
                    "active": record.profile_id == active_profile_id,
                    "profile": record.profile.model_dump(mode="json"),
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        return payloads

    async def set_active_candidate_profile(
        self,
        tenant_id: str,
        user_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        record = await runtime.store.get_candidate_profile(user_id, profile_id)
        if record is None:
            msg = f"Unknown profile_id: {profile_id}"
            raise KeyError(msg)
        await runtime.store.set_active_candidate_profile(user_id, profile_id)
        profiles = await self.list_candidate_profiles(tenant_id, user_id)
        for profile in profiles:
            if profile["profile_id"] == profile_id:
                return profile
        msg = f"Failed to activate candidate profile: {profile_id}"
        raise RuntimeError(msg)

    async def _resolve_candidate_profile(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> ManagedCandidateProfile | None:
        runtime = self.get_runtime(tenant_id)
        if user_id is None:
            return None
        resolved_profile_id = profile_id or await runtime.store.get_active_candidate_profile_id(
            user_id
        )
        if resolved_profile_id is None:
            return None
        return await runtime.store.get_candidate_profile(user_id, resolved_profile_id)

    async def _rerank_groups_for_profile(
        self,
        groups: list[JobGroup],
        *,
        tenant_id: str,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> list[JobGroup]:
        record = await self._resolve_candidate_profile(
            tenant_id,
            user_id=user_id,
            profile_id=profile_id,
        )
        if record is None:
            return groups

        from job_ftch.adapters.profile_inputs import build_profile_catalog
        from job_ftch.nodes.match_scoring import MultiProfileMatchNode

        node = MultiProfileMatchNode(build_profile_catalog(record.profile))
        scored: list[tuple[float, JobGroup]] = []
        for group in groups:
            job = await node.process(group.canonical_job)
            if job is None:
                continue
            scored_group = group.model_copy(update={"canonical_job": job})
            scored.append((float(job.best_score or 0.0), scored_group))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [group for _, group in scored]

    async def search_jobs(
        self,
        query: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        profile_id: str | None = None,
        limit: int = 20,
    ) -> list[JobGroup]:
        limit = min(limit, 100)
        tenant_ids = self.tenant_ids()
        if tenant_id is not None:
            groups = await self.get_runtime(tenant_id).search_backend.search(query, limit=limit)
            return await self._rerank_groups_for_profile(
                groups,
                tenant_id=tenant_id,
                user_id=user_id,
                profile_id=profile_id,
            )
        if len(tenant_ids) > 1:
            msg = "tenant_id is required when multiple tenants are configured"
            raise ValueError(msg)
        merged: list[JobGroup] = []
        seen: set[str] = set()
        for current_tenant in tenant_ids:
            groups = await self.get_runtime(current_tenant).search_backend.search(
                query, limit=limit
            )
            for group in groups:
                if group.group_id in seen:
                    continue
                seen.add(group.group_id)
                merged.append(group)
                if len(merged) >= limit:
                    return await self._rerank_groups_for_profile(
                        merged,
                        tenant_id=current_tenant,
                        user_id=user_id,
                        profile_id=profile_id,
                    )
        return await self._rerank_groups_for_profile(
            merged,
            tenant_id=tenant_ids[0],
            user_id=user_id,
            profile_id=profile_id,
        )

    async def get_job(self, job_id: str, *, tenant_id: str | None = None) -> JobRecord | None:
        if tenant_id is not None:
            return await self.get_runtime(tenant_id).job_backend.get_job(job_id)
        for current_tenant in self.tenant_ids():
            job = await self.get_runtime(current_tenant).job_backend.get_job(job_id)
            if job is not None:
                return job
        return None

    async def get_job_lineage(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
    ) -> JobLineage | None:
        async def _lineage_for_runtime(current_tenant: str) -> JobLineage | None:
            runtime = self.get_runtime(current_tenant)
            job = await runtime.job_backend.get_job(job_id)
            if job is None:
                return None
            group = None
            if job.group_id is not None:
                group = await runtime.job_group_store.get_group(job.group_id)
            return build_job_lineage(job, tenant_id=current_tenant, group=group)

        if tenant_id is not None:
            return await _lineage_for_runtime(tenant_id)
        for current_tenant in self.tenant_ids():
            lineage = await _lineage_for_runtime(current_tenant)
            if lineage is not None:
                return lineage
        return None

    async def latest_jobs(
        self,
        tenant_id: str,
        *,
        limit: int = 10,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> list[JobRecord]:
        groups = await self.get_runtime(tenant_id).job_group_store.list_groups(limit=limit)
        ranked = await self._rerank_groups_for_profile(
            groups,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
        )
        return [group.canonical_job for group in ranked]

    async def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        limit: int = 20,
    ) -> list[RunSummary]:
        limit = min(max(limit, 0), 100)
        if tenant_id is not None:
            return await self.get_runtime(tenant_id).store.list_run_summaries(limit=limit)
        summaries: list[RunSummary] = []
        for current_tenant in self.tenant_ids():
            summaries.extend(
                await self.get_runtime(current_tenant).store.list_run_summaries(limit=limit)
            )
        summaries.sort(key=_summary_sort_key, reverse=True)
        return summaries[:limit]

    async def get_run(
        self,
        run_id: str,
        *,
        tenant_id: str | None = None,
    ) -> RunSummary | None:
        if tenant_id is not None:
            return await self.get_runtime(tenant_id).store.get_run_summary(run_id)
        for current_tenant in self.tenant_ids():
            summary = await self.get_runtime(current_tenant).store.get_run_summary(run_id)
            if summary is not None:
                return summary
        return None

    async def get_config(self, tenant_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        tenant = runtime.tenant
        payload = tenant.model_dump(mode="json")
        payload.pop("auth_provider", None)
        return payload

    async def reset_tenant(self, tenant_id: str) -> None:
        runtime = self.get_runtime(tenant_id)
        await runtime.store.reset_namespace()
        runtime.runtime_sources.clear()
        runtime.disabled_source_ids.clear()
        runtime.sources_loaded = False
        self._apply_runtime_sources(runtime)

    async def close(self) -> None:
        closed: set[int] = set()
        for runtime in self._runtimes.values():
            for obj in (
                runtime.store,
                runtime.search_backend,
                runtime.job_backend,
                runtime.job_group_store,
            ):
                ident = id(obj)
                if ident in closed:
                    continue
                close = getattr(obj, "close", None)
                if callable(close):
                    await close()
                closed.add(ident)


@asynccontextmanager
async def _tenant_run_lock(settings: Settings, tenant_id: str) -> Any:
    lock_dir = settings.store_path.parent / "tenant_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{tenant_id}.lock"
    local_lock = _PROCESS_TENANT_LOCKS.setdefault(str(lock_path), asyncio.Lock())
    deadline = time.monotonic() + 30.0

    def _acquire() -> int:
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                return fd
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Could not acquire run lock for {tenant_id} after 30s"
                    ) from None
                try:
                    pid_bytes = lock_path.read_bytes()
                    stale_pid = int(pid_bytes.strip())
                except ValueError:
                    logger.warning("reclaiming_stale_lock", tenant_id=tenant_id)
                    try:
                        lock_path.unlink(missing_ok=True)
                    except PermissionError:
                        time.sleep(0.1)
                        continue
                    continue
                try:
                    os.kill(stale_pid, 0)
                except ProcessLookupError:
                    logger.warning("reclaiming_stale_lock", tenant_id=tenant_id)
                    try:
                        lock_path.unlink(missing_ok=True)
                    except PermissionError:
                        time.sleep(0.1)
                        continue
                    continue
                except OSError:
                    time.sleep(0.1)
                    continue
                time.sleep(0.1)

    fd: int | None = None
    async with local_lock:
        try:
            fd = await asyncio.to_thread(_acquire)
            yield
        finally:
            if fd is not None:
                os.close(fd)
                lock_path.unlink(missing_ok=True)
