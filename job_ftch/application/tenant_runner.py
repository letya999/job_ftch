"""Multi-tenant pipeline orchestration and tenant-scoped service helpers."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
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
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.watermark import IncrementalCursor
from job_ftch.application.registry import (
    create_job_backend,
    create_job_group_store,
    create_llm,
    create_search_backend,
    create_store,
)
from job_ftch.config import get_settings
from job_ftch.domain import (
    JobGroup,
    JobLineage,
    JobRecord,
    TenantConfig,
    TenantInfo,
    build_job_lineage,
)
from job_ftch.infrastructure.metrics.prometheus import PrometheusExporter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.application.contracts import (
        JobGroupStore,
        JobPersistenceBackend,
        LLMProvider,
        ProcessingNode,
        SearchBackend,
        Store,
        StoreConnector,
    )
    from job_ftch.config import Settings

logger = structlog.get_logger(__name__)


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
            try:
                payload[key] = datetime.fromisoformat(value)
            except ValueError:
                pass
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
        raw = json.dumps(summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True)
        await connector.set(self._key(f"run_history:{run_id}"), raw)
        await connector.set_add(self._key("run_history_ids"), run_id)

    async def get_run_summary(self, run_id: str) -> RunSummary | None:
        connector = cast("StoreConnector", self._store)
        raw = await connector.get(self._key(f"run_history:{run_id}"))
        if raw is None:
            return None
        try:
            return _summary_from_payload(json.loads(raw), tenant_id=self._tenant_id)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("run_history_decode_failed", tenant_id=self._tenant_id, run_id=run_id, error=str(exc))
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
    store: TenantStore
    builder: PipelineBuilder
    job_group_store: JobGroupStore
    search_backend: SearchBackend
    job_backend: JobPersistenceBackend
    metrics_exporter: PrometheusExporter | None = None


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
        metrics_exporters_by_port: dict[int, PrometheusExporter] = {}
        for tenant in tenants:
            tenant_settings = tenant_to_settings(tenant, settings_template)
            metrics_exporter: PrometheusExporter | None = None
            if tenant_settings.metrics_enabled:
                metrics_exporter = metrics_exporters_by_port.get(tenant_settings.metrics_port)
                if metrics_exporter is None:
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
            builder.stage(cast("ProcessingNode[Any]", sanitize_node))
            for node in nodes:
                builder.stage(cast("ProcessingNode[Any]", node))
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
                store=tenant_store,
                builder=builder,
                job_group_store=job_group_store,
                search_backend=cast("SearchBackend", create_search_backend(tenant_settings)),
                job_backend=cast("JobPersistenceBackend", create_job_backend(tenant_settings)),
                metrics_exporter=metrics_exporter,
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

    async def run_tenant(self, tenant_id: str, *, max_items: int | None = None) -> RunSummary:
        runtime = self.get_runtime(tenant_id)
        async with _tenant_run_lock(runtime.settings, tenant_id):
            summary = await runtime.builder.clone().run_async(max_items=max_items)
        summary.tenant_id = tenant_id
        await runtime.store.set_run_state(
            "pipeline.run_summary",
            json.dumps(
                summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True
            ),
        )
        await runtime.store.save_run_summary(summary)
        if runtime.metrics_exporter is not None:
            await runtime.metrics_exporter.observe_run(
                summary,
                tenant_id=tenant_id,
                job_group_total=await runtime.job_group_store.count(),
            )
        return summary

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

    async def search_jobs(
        self,
        query: str,
        *,
        tenant_id: str | None = None,
        limit: int = 20,
    ) -> list[JobGroup]:
        limit = min(limit, 100)
        tenant_ids = self.tenant_ids()
        if tenant_id is not None:
            return await self.get_runtime(tenant_id).search_backend.search(query, limit=limit)
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
                    return merged
        return merged

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

    async def latest_jobs(self, tenant_id: str, *, limit: int = 10) -> list[JobRecord]:
        groups = await self.get_runtime(tenant_id).job_group_store.list_groups(limit=limit)
        return [group.canonical_job for group in groups]

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
            summaries.extend(await self.get_runtime(current_tenant).store.list_run_summaries(limit=limit))
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
        tenant = self.get_runtime(tenant_id).tenant
        payload = tenant.model_dump(mode="json")
        payload.pop("auth_provider", None)
        return payload

    async def reset_tenant(self, tenant_id: str) -> None:
        runtime = self.get_runtime(tenant_id)
        await runtime.store.reset_namespace()

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
    try:
        fd = await asyncio.to_thread(_acquire)
        yield
    finally:
        if fd is not None:
            os.close(fd)
            lock_path.unlink(missing_ok=True)
