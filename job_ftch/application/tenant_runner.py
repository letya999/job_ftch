"""Multi-tenant pipeline orchestration and tenant-scoped service helpers."""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from contextlib import nullcontext, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import structlog
from opentelemetry import trace
from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.builder import (
    PipelineBuilder,
    build_delivery_targets,
    build_llm,
    build_nodes,
    build_output_sinks,
    build_quarantine_sink,
    build_rejected_sink,
    build_v2_typed_bindings,
    load_profile_catalog,
    resolve_settings_pipeline_item_concurrency,
    resolve_settings_source_fetch_concurrency,
    resolve_settings_source_preparation_concurrency,
    tenant_to_settings,
)
from job_ftch.application.llm_usage import collect_llm_usage, pricing_version
from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.registry import (
    create_embedding_provider,
    create_job_backend,
    create_job_group_store,
    create_llm,
    create_ontology_store,
    create_search_backend,
    create_store,
    create_vector_backend,
)
from job_ftch.application.source_assessment import (
    create_source_assessment_service,
    load_source_assessment,
)
from job_ftch.config import Settings, get_settings
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
from job_ftch.domain.browser_capability_inventory import (
    BrowserCapabilityInventory,
    RoutePlanExplanation,
)
from job_ftch.domain.source_assessment import SourceAssessmentResult, SourceIngestState


class OperatorSessionAttachError(Exception):
    """Borrow of an operator browser session failed before ingest."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("error") or "session_attach_failed"))


if TYPE_CHECKING:
    from collections.abc import Sequence

    from job_ftch.application.contracts import (
        BgeMThreeProviderPort,
        JobGroupStore,
        JobPersistenceBackend,
        LLMProvider,
        SearchBackend,
        Store,
        VectorBackend,
    )
    from job_ftch.domain.public_source_registry import PublicSourceRegistry
    from job_ftch.domain.source_spec import SourceSpec
    from job_ftch.nodes.snapshot_filter import SnapshotFilterNode

logger = structlog.get_logger(__name__)
_DEFAULT_LATEST_JOBS_POOL = 200
_PROFILE_AWARE_LATEST_JOBS_POOL = 1000


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


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    with suppress(ValueError, TypeError):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


async def _read_runtime_state(runtime: TenantRuntime, key: str) -> str | None:
    value = await runtime.store.get_run_state(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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


def _active_candidate_profiles_key(user_id: str) -> str:
    return f"candidate_profile_active_ids:{user_id}"


def _update_source_health_payload(
    previous: SourceHealth | None,
    *,
    source_id: str,
    source_kind: str,
    source_name: str,
    stats: SourceRunStats,
    started_at: datetime | None = None,
    finished_at: datetime,
    drift_ratio_threshold: float = 0.2,
    min_baseline_threshold: float = 3.0,
    majority_failure_ratio: float = 0.5,
) -> SourceHealth:
    prev_baseline_emitted = previous.baseline_emitted if previous else 0.0
    previous_success = previous.success_count if previous else 0
    current_emitted = int(stats.emitted)
    # Only count as a source-level failure when the source itself crashed (nothing fetched)
    # or when the majority of fetched items failed — not for incidental item-level errors.
    #
    # `failed` is a subset of `fetched`: every pulled observation increments `fetched`
    # first, and an item that later errors also increments `failed`. The item-failure
    # ratio is therefore failed/fetched, never failed/(fetched+failed) — the latter
    # double-counts the failed items in the denominator and can never cross 0.5, so the
    # majority branch was effectively unreachable.
    if stats.fetched > 0 and stats.failed > stats.fetched:
        # Item-level failures are a subset of fetched, so failed <= fetched must hold
        # once anything was fetched. `fetched == 0 and failed > 0` is a normal
        # source-level failure (the source crashed before pulling items), not an
        # accounting bug, so only a non-zero fetched with failed > fetched is surfaced.
        logger.warning(
            "source_health_counter_invariant_violated",
            source_id=source_id,
            fetched=stats.fetched,
            failed=stats.failed,
        )
    had_failure = (stats.fetched == 0 and stats.failed > 0) or (
        stats.fetched > 1 and stats.failed / stats.fetched > majority_failure_ratio
    )
    degraded = False
    drift_ratio: float | None = None
    if prev_baseline_emitted >= min_baseline_threshold:
        drift_ratio = current_emitted / prev_baseline_emitted if prev_baseline_emitted > 0 else 0.0
        degraded = current_emitted == 0 or drift_ratio < drift_ratio_threshold

    if had_failure:
        failure_streak = (previous.failure_streak if previous else 0) + 1
        last_success_at = previous.last_success_at if previous else None
        last_started_at = previous.last_started_at if previous else None
        success_count = previous_success
        next_baseline = prev_baseline_emitted
    else:
        failure_streak = 0
        last_started_at = started_at.isoformat() if started_at is not None else None
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
        last_started_at=last_started_at,
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
        status=(
            "degraded"
            if degraded
            else (
                "failing" if had_failure else "healthy"
            )
        ),
        paused=False,
        skipped_runs=0,
        last_eviction_at=previous.last_eviction_at if previous else None,
        eviction_streak=previous.eviction_streak if previous else 0,
        last_eviction_kind=previous.last_eviction_kind if previous else None,
        last_error=None,
        last_error_at=None,
        last_error_kind=None,
    )


def _canonical_base_source_id(
    runtime: TenantRuntime,
    source_id: str,
    listed_item: dict[str, Any] | None,
) -> str | None:
    base_by_id = {source_spec_identifier(spec): spec for spec in runtime.base_sources}
    if source_id in base_by_id:
        return source_id
    if listed_item is None:
        return None
    name = listed_item.get("source_name")
    locator = listed_item.get("locator")
    for ident, spec in base_by_id.items():
        if source_spec_name(spec) == name or source_spec_locator(spec) == locator:
            return ident
    return None


def _listing_payload_for_source(
    payloads: list[dict[str, Any]],
    source_id: str,
    runtime: TenantRuntime,
) -> dict[str, Any] | None:
    for payload in payloads:
        if payload.get("source_id") == source_id:
            return payload
    name = None
    for spec in runtime.base_sources:
        if source_spec_identifier(spec) == source_id:
            name = source_spec_name(spec)
            break
    if name is None:
        return None
    for payload in payloads:
        if payload.get("origin") == "config" and payload.get("source_name") == name:
            return payload
    return None


def _build_source_listing_payload(
    spec: SourceSpec,
    *,
    origin: str,
    enabled: bool,
    health: SourceHealth | None,
) -> dict[str, Any]:
    monitor = getattr(spec, "monitor", None)
    monitor_config = getattr(spec, "monitor_config", {}) or {}
    browser_required = (
        spec.type == "browser"
        or bool(monitor_config.get("render"))
        or monitor
        in {
            "api_sniffer",
            "browser",
        }
    )
    browser_reason = None
    browser_setup_hint = None
    if browser_required:
        browser_reason = (
            "render=true"
            if monitor_config.get("render")
            else ("browser source" if spec.type == "browser" else f"monitor={monitor}")
        )
        browser_setup_hint = "Requires Playwright + Chromium in the runtime image/environment."

    source_id = source_spec_identifier(spec)
    payload: dict[str, Any] = {
        "source_id": source_id,
        "source_kind": spec.type,
        "source_name": source_spec_name(spec),
        "locator": source_spec_locator(spec),
        "origin": origin,
        "enabled": enabled,
        "spec": spec.model_dump(mode="json"),
        "requirements": {
            "browser_required": browser_required,
            "browser_reason": browser_reason,
            "browser_setup_hint": browser_setup_hint,
        },
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


def _effective_limit_cap(limit: int | None, cap: int) -> int:
    return min(limit, cap) if limit is not None else cap


def _resolve_source_interval_seconds(runtime: TenantRuntime, spec: SourceSpec) -> int:
    if spec.interval_seconds:
        return int(spec.interval_seconds)
    if runtime.tenant.schedule and runtime.tenant.schedule.interval_seconds:
        return int(runtime.tenant.schedule.interval_seconds)
    if runtime.settings.schedule_interval_seconds:
        return int(runtime.settings.schedule_interval_seconds)
    return 4 * 60 * 60


def _apply_runtime_fetch_window(
    spec: SourceSpec,
    *,
    assessment: object | None,
    bootstrap_completed_at: datetime | None,
    last_started_at: datetime | None = None,
    last_successful_run_at: datetime | None = None,
    interval_seconds: int,
    now: datetime,
) -> SourceSpec:
    from job_ftch.domain.source_spec import (
        CareerSiteSpec,
        RSSFeedSourceSpec,
        TelegramChannelSpec,
        TelegramCommentsSpec,
        TelegramGroupSpec,
    )

    result = assessment if isinstance(assessment, SourceAssessmentResult) else None
    freshness = result.freshness if result is not None else None
    can_use_time_window = bool(
        freshness
        and freshness.can_detect_freshness_without_snapshot
        and (freshness.item_level_dates or freshness.can_filter_since_yesterday)
    )
    bootstrap_mode = getattr(spec, "initial_ingest_mode", "auto")
    bootstrap_limit = int(getattr(spec, "initial_ingest_max_items", 50) or 50)
    bootstrap_lookback = int(
        getattr(spec, "initial_ingest_lookback_seconds", 7 * 24 * 60 * 60) or 7 * 24 * 60 * 60
    )

    def _clear_fetch_limits(update: dict[str, object]) -> None:
        if isinstance(spec, CareerSiteSpec):
            update["limit"] = None
            if not getattr(freshness, "dates_require_detail_scrape", False):
                update["detail_limit"] = None
        elif isinstance(spec, (TelegramChannelSpec, TelegramGroupSpec)):
            update["limit"] = None
        elif isinstance(spec, TelegramCommentsSpec):
            update["post_limit"] = None
            update["comment_limit_per_post"] = None

    def _restore_fetch_limits(update: dict[str, object]) -> None:
        if isinstance(spec, CareerSiteSpec):
            update["limit"] = spec.limit
            update["detail_limit"] = spec.detail_limit
        elif isinstance(spec, (TelegramChannelSpec, TelegramGroupSpec)):
            update["limit"] = spec.limit
        elif isinstance(spec, TelegramCommentsSpec):
            update["post_limit"] = spec.post_limit
            update["comment_limit_per_post"] = spec.comment_limit_per_post

    supports_runtime_window = isinstance(
        spec,
        (
            CareerSiteSpec,
            RSSFeedSourceSpec,
            TelegramChannelSpec,
            TelegramCommentsSpec,
            TelegramGroupSpec,
        ),
    )
    if not supports_runtime_window:
        return spec

    update: dict[str, object] = {}
    if bootstrap_completed_at is None:
        if can_use_time_window and bootstrap_mode in {"auto", "lookback_window"}:
            _clear_fetch_limits(update)
            update["freshness_cutoff_utc"] = now - timedelta(seconds=bootstrap_lookback)
        else:
            if isinstance(spec, CareerSiteSpec):
                update["limit"] = bootstrap_limit
                update["detail_limit"] = bootstrap_limit
            update["freshness_cutoff_utc"] = None
        return spec.model_copy(update=update)

    if can_use_time_window:
        cutoff = (
            last_started_at
            or last_successful_run_at
            or (now - timedelta(seconds=max(interval_seconds, 1)))
        )
        if cutoff > now:
            cutoff = now
        _clear_fetch_limits(update)
        update["freshness_cutoff_utc"] = cutoff
        return spec.model_copy(update=update)

    _restore_fetch_limits(update)
    update["freshness_cutoff_utc"] = None
    return spec.model_copy(update=update)


async def _attach_source_assessment(
    runtime: TenantRuntime, payload: dict[str, Any]
) -> dict[str, Any]:
    source_id = str(payload["source_id"])
    result = await load_source_assessment(runtime.store, source_id)
    if result is None:
        payload["assessment"] = {"status": "missing"}
        return payload
    recommended_monitors: list[str] = []
    for item in result.evidence:
        details = item.details if hasattr(item, "details") else {}
        monitors = details.get("recommended_monitors") if isinstance(details, dict) else None
        if isinstance(monitors, list) and monitors:
            recommended_monitors = [str(m) for m in monitors if str(m)]
            break
    payload["assessment"] = {
        "status": "assessed",
        "confidence": result.freshness.confidence.value,
        "can_detect_freshness_without_snapshot": (
            result.freshness.can_detect_freshness_without_snapshot
        ),
        "can_filter_since_yesterday": result.freshness.can_filter_since_yesterday,
        "item_level_dates": result.freshness.item_level_dates,
        "ordered_by_newest": result.freshness.ordered_by_newest,
        "page_level_change_only": result.freshness.page_level_change_only,
        "requires_full_snapshot": result.freshness.requires_full_snapshot,
        "probe_failed": result.freshness.probe_failed,
        "probe_blocked": result.freshness.probe_blocked,
        "rationale": result.freshness.rationale,
        "recommended_monitors": recommended_monitors,
        "assessed_at": result.assessed_at.isoformat(),
        "capabilities": result.capabilities.model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in result.evidence],
    }
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


# Re-exported for backward compatibility. The full implementation moved to
# `job_ftch.application.tenant_store` as part of the v0.0.4 god-object split.
import job_ftch.application.tenant_locks as _tenant_locks  # noqa: E402
import job_ftch.application.tenant_store as _tenant_store  # noqa: E402
from job_ftch.application.tenant_runtime import TenantRuntime  # noqa: E402, F401

TenantRunAlreadyActiveError = _tenant_locks.TenantRunAlreadyActiveError
TenantRunLockError = _tenant_locks.TenantRunLockError
_tenant_run_lock = _tenant_locks.tenant_run_lock
TenantStore = _tenant_store.TenantStore
_summary_from_payload = _tenant_store._summary_from_payload


def _pin_parser(spec: SourceSpec, parser_override: str) -> SourceSpec:
    name = parser_override.strip()
    spec_type = getattr(spec, "type", None)
    updates: dict[str, Any] = {}
    if spec_type == "declarative_html":
        updates["parser_kind"] = name
    elif spec_type == "browser":
        updates["parser"] = name
    elif spec_type == "career_site":
        from job_ftch.application.registry import (
            all_monitor_names,
            all_scraper_names,
            all_site_parser_names,
        )

        if name in all_site_parser_names():
            updates["site_parser"] = name
        if name in all_monitor_names():
            updates["monitor"] = name
        if name in all_scraper_names():
            updates["scraper"] = name
        if not updates:
            raise ValueError(
                f"parser {name!r} is not a registered monitor, scraper, or site parser"
            )
    else:
        raise ValueError(f"parser pin is not supported for source type {spec_type}")
    copier = getattr(spec, "model_copy", None)
    if not callable(copier):
        return spec
    return spec.model_copy(update=updates)


class TenantRunner:
    def __init__(self, runtimes: dict[str, TenantRuntime]) -> None:
        self._runtimes = runtimes
        self._operator_sessions: Any = None

    @classmethod
    def from_tenants(
        cls,
        tenants: Sequence[TenantConfig],
        *,
        base_settings: Settings | None = None,
        bgem3_provider: BgeMThreeProviderPort | None = None,
    ) -> TenantRunner:
        settings_template = base_settings or get_settings()
        runtimes: dict[str, TenantRuntime] = {}
        for tenant in tenants:
            tenant_settings = tenant_to_settings(tenant, settings_template)
            auth = resolve_auth_provider(tenant.auth_provider, settings=tenant_settings)
            base_store = cast("Store", create_store(tenant_settings))
            tenant_store = TenantStore(tenant.tenant_id, base_store)
            job_group_store = cast("JobGroupStore", create_job_group_store(tenant_settings))
            llm = cast("LLMProvider", create_llm(tenant_settings))
            embedding_provider = None
            # The generic embedding provider is only consumed by the optional
            # semantic prefilter. Do not load its heavyweight model merely to
            # start an ingest graph that has the prefilter disabled.
            if (
                tenant_settings.embedding_enabled
                and tenant_settings.embedding_prefilter_enabled
                and tenant_settings.embedding_provider
            ):
                try:
                    embedding_provider = create_embedding_provider(tenant_settings)
                except Exception as exc:  # noqa: BLE001 - optional provider must not block bot startup
                    import structlog as _sl

                    _sl.get_logger("job_ftch.tenant_runner").warning(
                        "embedding_provider_init_failed", error=str(exc)
                    )
                    embedding_provider = None
            ontology_store = None
            try:
                ontology_store = create_ontology_store(tenant_settings)
            except Exception:
                ontology_store = None
            # BGE-M3 provider: instantiated once at startup so the
            # bot adapter and the pipeline builder share the same
            # encoder (and therefore the same vector dim). Without
            # this, the bot's ad-hoc encodings and the pipeline's
            # encodings could end up at different dims, which would
            # crash the relevance scorer with a shape mismatch.
            tenant_bgem3_provider = bgem3_provider
            if tenant_bgem3_provider is None and getattr(tenant_settings, "bgem3_enabled", False):
                try:
                    from job_ftch.infrastructure.embeddings.bgem3 import (
                        BgeMThreeProvider,
                    )

                    tenant_bgem3_provider = BgeMThreeProvider(tenant_settings.bgem3_model)
                except Exception as exc:  # noqa: BLE001
                    import structlog as _sl

                    _sl.get_logger("job_ftch.tenant_runner").warning(
                        "bgem3_provider_init_failed",
                        error=str(exc),
                    )
                    tenant_bgem3_provider = None
            catalog = load_profile_catalog(tenant_settings)
            sanitize_node, _snapshot_filter, nodes = build_nodes(
                tenant_settings,
                tenant_store,
                llm,
                job_group_store,
                catalog=catalog,
                bgem3_provider=tenant_bgem3_provider,
            )
            output_sink, main_sink, review_sink, posting_sink = build_output_sinks(tenant_settings)
            rejected_counted, rejected_sink = build_rejected_sink(tenant_settings)
            builder = PipelineBuilder()
            builder.sources(tenant.sources)
            builder.auth(auth)
            builder.store(tenant_store)
            builder.stage(sanitize_node)
            for node in nodes:
                builder.stage(node)
            builder.sink(output_sink)
            builder.with_delivery_targets(build_delivery_targets(tenant_settings, posting_sink))
            builder.with_quarantine_sink(build_quarantine_sink(tenant_settings))
            builder.with_rejected_sink(rejected_sink, counted=rejected_counted)
            builder.set_default_max_items(tenant_settings.pipeline_max_items_per_run)
            builder.set_summary_context(
                output_sink=main_sink,
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
                llm_provider=llm,
                job_group_store=job_group_store,
                search_backend=cast("SearchBackend", create_search_backend(tenant_settings)),
                job_backend=cast("JobPersistenceBackend", create_job_backend(tenant_settings)),
                vector_backend=cast("VectorBackend", create_vector_backend(tenant_settings))
                if embedding_provider is not None and tenant_settings.vector_backend
                else None,
                embedding_provider=embedding_provider,
                ontology_store=ontology_store,
                bgem3_provider=tenant_bgem3_provider,
                base_sources=tuple(tenant.sources),
            )
            # Bind the in-memory BGE-M3 shot store to the process
            # registry so the bot adapter can push user shots into
            # the same store the pipeline builder reads from. This
            # is the single line that fixes the "user's shots
            # ignored at pipeline time" bug.
            if tenant_bgem3_provider is not None:
                try:
                    from job_ftch.infrastructure.relevance import shot_registry
                    from job_ftch.infrastructure.relevance.shot_anchor import (
                        InMemoryBgeMThreeShotStore,
                    )

                    _store = InMemoryBgeMThreeShotStore(provider=tenant_bgem3_provider)
                    shot_registry.configure(
                        store=_store,
                        provider=tenant_bgem3_provider,
                    )
                except Exception as exc:  # noqa: BLE001 - non-fatal
                    import structlog as _sl

                    _sl.get_logger("job_ftch.tenant_runner").warning(
                        "shot_registry_configure_failed",
                        error=str(exc),
                    )
        return cls(runtimes)

    def tenant_ids(self) -> list[str]:
        return sorted(self._runtimes)

    def _control_runtime(self) -> TenantRuntime:
        return self.get_runtime(self.default_tenant_id())

    async def get_selected_tenant_id(self, user_id: str | None) -> str:
        if not user_id:
            return self.default_tenant_id()
        runtime = self._control_runtime()
        raw = await runtime.store.get_run_state(f"config:telegram_selected_tenant:{user_id}")
        if raw and raw in self._runtimes:
            return raw
        return self.default_tenant_id()

    async def set_selected_tenant_id(self, user_id: str, tenant_id: str) -> str:
        if tenant_id not in self._runtimes:
            msg = f"Unknown tenant_id: {tenant_id}"
            raise KeyError(msg)
        runtime = self._control_runtime()
        await runtime.store.set_run_state(f"config:telegram_selected_tenant:{user_id}", tenant_id)
        return tenant_id

    def get_runtime(self, tenant_id: str) -> TenantRuntime:
        runtime = self._runtimes.get(tenant_id)
        if runtime is None:
            msg = f"Unknown tenant_id: {tenant_id}"
            raise KeyError(msg)
        return runtime

    async def _ensure_runtime_sources_loaded(self, runtime: TenantRuntime) -> None:
        if runtime.sources_loaded:
            return
        await self._reload_runtime_sources(runtime)
        await self._ensure_dynamic_config_loaded(runtime)
        runtime.sources_loaded = True

    async def _reload_runtime_sources(self, runtime: TenantRuntime) -> None:
        runtime.runtime_sources = {
            record.source_id: record for record in await runtime.store.list_runtime_sources()
        }
        runtime.disabled_source_ids = await runtime.store.list_disabled_source_ids()
        self._apply_runtime_sources(runtime)

    async def _ensure_dynamic_config_loaded(self, runtime: TenantRuntime) -> None:
        posting_backend = await runtime.store.get_run_state("config:posting_backend")
        posting_entity = await runtime.store.get_run_state("config:telegram_publish_entity")
        notify_mode = await runtime.store.get_run_state("config:notify_mode")
        notify_batch_size = await runtime.store.get_run_state("config:notify_batch_size")

        updated = False
        if posting_backend and posting_backend != runtime.settings.posting_backend:
            runtime.settings.posting_backend = posting_backend
            updated = True
        if posting_entity and posting_entity != runtime.settings.telegram_publish_entity:
            runtime.settings.telegram_publish_entity = posting_entity
            updated = True
        if notify_mode and notify_mode != runtime.settings.notify_mode:
            runtime.settings.notify_mode = notify_mode
            updated = True
        if notify_batch_size:
            try:
                val = int(notify_batch_size)
                if val != runtime.settings.notify_batch_size:
                    runtime.settings.notify_batch_size = val
                    updated = True
            except ValueError:
                pass

        if updated:
            output_sink, main_sink, review_sink, posting_sink = build_output_sinks(runtime.settings)
            runtime.builder.clear_sinks().sink(output_sink)
            runtime.builder.with_delivery_targets(
                build_delivery_targets(runtime.settings, posting_sink)
            )
            runtime.builder.set_summary_context(
                output_sink=main_sink,
                review_sink=review_sink,
                posting_sink=posting_sink,
                job_group_store=runtime.job_group_store,
                profile_name=load_profile_catalog(runtime.settings).catalog_name,
                output_path=runtime.settings.output_path,
            )

    async def _get_source_bootstrap_completed_at(
        self, runtime: TenantRuntime, source_id: str
    ) -> datetime | None:
        state = await runtime.store.get_source_ingest_state(runtime.tenant.tenant_id, source_id)
        return state.bootstrap_completed_at if state is not None else None

    async def _mark_source_bootstrap_completed(
        self, runtime: TenantRuntime, source_id: str, completed_at: datetime
    ) -> None:
        await runtime.store.save_source_ingest_state(
            runtime.tenant.tenant_id,
            SourceIngestState(
                source_id=source_id,
                bootstrap_completed_at=completed_at,
                updated_at=completed_at,
            ),
        )

    async def get_publish_channel(self, tenant_id: str) -> str | None:
        runtime = self.get_runtime(tenant_id)
        raw = await runtime.store.get_run_state("config:telegram_publish_entity")
        return raw if raw else None

    async def get_publish_user_id(self, tenant_id: str) -> str | None:
        runtime = self.get_runtime(tenant_id)
        raw = await runtime.store.get_run_state("config:telegram_publish_user_id")
        return raw if raw else None

    async def set_publish_channel(
        self,
        tenant_id: str,
        channel: str | None,
        *,
        user_id: str | None = None,
    ) -> None:
        runtime = self.get_runtime(tenant_id)
        # Bot-owned channel publishing uses Bot API sends in the adapter. Keep the
        # core Telethon posting sink disabled to avoid duplicate side effects.
        await runtime.store.set_run_state("config:posting_backend", "none")
        if channel is None:
            await runtime.store.set_run_state("config:telegram_publish_entity", "")
            await runtime.store.set_run_state("config:telegram_publish_user_id", "")
        else:
            await runtime.store.set_run_state("config:telegram_publish_entity", channel)
            await runtime.store.set_run_state("config:telegram_publish_user_id", user_id or "")
        runtime.sources_loaded = False
        await self._ensure_runtime_sources_loaded(runtime)

    async def get_schedule_interval(self, tenant_id: str) -> int | None:
        runtime = self.get_runtime(tenant_id)
        raw = await runtime.store.get_run_state("config:schedule_interval_seconds")
        if raw == "off":
            return None
        if not raw:
            if runtime.tenant.schedule is not None:
                return runtime.tenant.schedule.interval_seconds
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    async def set_schedule_interval(self, tenant_id: str, seconds: int | None) -> None:
        runtime = self.get_runtime(tenant_id)
        value = str(seconds) if seconds is not None else "off"
        await runtime.store.set_run_state("config:schedule_interval_seconds", value)

    async def get_bot_scheduler_status(self, tenant_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        return {
            "last_attempt_at": await _read_runtime_state(runtime, "bot_scheduler:last_attempt_at"),
            "last_success_at": await _read_runtime_state(runtime, "bot_scheduler:last_success_at"),
            "last_error": await _read_runtime_state(runtime, "bot_scheduler:last_error"),
            "last_run_emitted": await _read_runtime_state(
                runtime, "bot_scheduler:last_run_emitted"
            ),
            "last_publish_attempt_at": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_attempt_at"
            ),
            "last_publish_success_at": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_success_at"
            ),
            "last_publish_error": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_error"
            ),
            "last_publish_sent": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_sent"
            ),
            "last_publish_skipped_at": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_skipped_at"
            ),
            "last_publish_skipped_reason": await _read_runtime_state(
                runtime, "bot_scheduler:last_publish_skipped_reason"
            ),
        }

    async def update_posting_config(self, tenant_id: str, channel: str) -> None:
        runtime = self.get_runtime(tenant_id)
        await runtime.store.set_run_state("config:posting_backend", "telegram_posting")
        await runtime.store.set_run_state("config:telegram_publish_entity", channel)
        # Force reload
        runtime.sources_loaded = False
        await self._ensure_runtime_sources_loaded(runtime)

    async def update_notify_config(
        self, tenant_id: str, mode: str, batch_size: int | None = None
    ) -> None:
        if mode not in ("instant", "digest"):
            msg = "notify_mode must be 'instant' or 'digest'"
            raise ValueError(msg)
        runtime = self.get_runtime(tenant_id)
        await runtime.store.set_run_state("config:notify_mode", mode)
        if batch_size is not None:
            await runtime.store.set_run_state("config:notify_batch_size", str(batch_size))
        # Force reload
        runtime.sources_loaded = False
        await self._ensure_runtime_sources_loaded(runtime)

    async def _build_runtime_catalog(
        self, runtime: TenantRuntime, *, user_id: str | None = None
    ) -> tuple[Any, dict[str, str | None]]:
        """Build runtime catalog with embedded shots and dynamic relevance prompts.

        Returns (catalog, relevance_prompts).
        BR-4: embed_profile_examples() called to refresh embedding_vector.
        BR-1: build_relevance_prompt_from_profile() generates dynamic prompts.
        """
        from job_ftch.application.profile_inputs import embed_search_profile
        from job_ftch.application.prompt_builder import build_relevance_prompts_for_catalog
        from job_ftch.domain import ProfileCatalog

        active_records = await runtime.store.list_all_active_candidate_profiles()
        if user_id is not None:
            active_records = [record for record in active_records if record.user_id == user_id]
        user_profiles = tuple(
            search_profile
            for record in active_records
            for search_profile in record.profile.search_profiles
        )

        if not user_profiles:
            fallback = load_profile_catalog(runtime.settings)
            return fallback, {}

        # BR-4: re-embed shots on every run
        if runtime.embedding_provider:
            embedded_profiles = []
            for sp in user_profiles:
                with suppress(Exception):
                    sp = await embed_search_profile(sp, runtime.embedding_provider)
                embedded_profiles.append(sp)
            user_profiles = tuple(embedded_profiles)

        catalog_name = f"user:{user_id}" if user_id is not None else "user"
        catalog = ProfileCatalog(catalog_name=catalog_name, profiles=user_profiles)

        # BR-1: generate dynamic relevance prompts from shots
        relevance_prompts: dict[str, str | None] = {}
        if runtime.llm_provider and runtime.store:
            try:
                relevance_prompts = await build_relevance_prompts_for_catalog(
                    catalog,
                    runtime.llm_provider,
                    runtime.store,
                )
            except Exception as exc:
                structlog.get_logger("job_ftch.tenant_runner").warning(
                    "relevance_prompts_build_failed", error=str(exc)
                )

        return catalog, relevance_prompts

    async def _build_runtime_builder(
        self,
        runtime: TenantRuntime,
        *,
        effective_sources: list[SourceSpec],
        catalog: Any,
        run_id: str,
        user_id: str | None = None,
        relevance_prompts: dict[str, str | None] | None = None,
        personal_mode: bool = False,
        personal_max_items: int | None = None,
    ) -> tuple[PipelineBuilder, SnapshotFilterNode | None]:
        # Load the live ontology from the Postgres/SQLite store
        # so the builder has both the static fixtures seed and
        # whatever the LLM has extracted from the user's recent
        # shots. Without this, the builder only ever saw the seed
        # and user-added roles like "Senior LLM Engineer" were
        # invisible to the role-anchors in the parallel scoring
        # node.
        from job_ftch.application.builder import (
            _build_runtime_ontology_payload,
            merge_derived_ontology,
        )
        from job_ftch.application.prompt_builder import build_relevance_prompts_for_catalog
        from job_ftch.application.search_expansion import expand_career_site_specs

        runtime_derived_ontology: dict[str, Any] = {}
        if runtime.ontology_store is not None:
            try:
                runtime_derived_ontology = await _build_runtime_ontology_payload(
                    runtime.ontology_store
                )
            except Exception as exc:  # noqa: BLE001
                structlog.get_logger("job_ftch.tenant_runner").warning(
                    "runtime_ontology_load_failed",
                    error=str(exc),
                )

        # V2 graphs construct their typed relevance nodes separately from
        # build_nodes(). Keep one enriched catalog for both paths; otherwise
        # only legacy nodes see live anti-patterns and role/skill additions.
        effective_catalog = merge_derived_ontology(catalog, runtime_derived_ontology)

        # Rewrite bare aggregator sources into keyword-filtered search URLs using
        # the tenant's target roles. Sources with an explicit query are untouched.
        effective_sources = expand_career_site_specs(
            effective_sources,
            tuple(
                role
                for profile in effective_catalog.profiles
                for role in profile.target_roles
                if role.strip()
            ),
        )
        if effective_catalog != catalog and runtime.llm_provider and runtime.store:
            try:
                relevance_prompts = await build_relevance_prompts_for_catalog(
                    effective_catalog,
                    runtime.llm_provider,
                    runtime.store,
                )
            except Exception as exc:  # noqa: BLE001
                structlog.get_logger("job_ftch.tenant_runner").warning(
                    "effective_relevance_prompts_build_failed",
                    error=str(exc),
                )

        sanitize_node, _snapshot_filter, nodes = build_nodes(
            runtime.settings,
            runtime.store,
            runtime.llm_provider,
            runtime.job_group_store,
            catalog=effective_catalog,
            tenant_id=runtime.tenant.tenant_id,
            run_id=run_id,
            user_id=user_id,
            ontology_store=runtime.ontology_store,
            relevance_prompts=relevance_prompts,
            derived_ontology_override=runtime_derived_ontology,
            personal_mode=personal_mode,
        )
        if runtime.settings.pipeline_graph_path is not None:
            from job_ftch.application.graph import build_v2_executor, compile_graph, load_graph
            from job_ftch.application.graph.pipeline_stage import GraphPipelineStage

            graph = compile_graph(load_graph(runtime.settings.pipeline_graph_path))
            from job_ftch.application.prefilter_artifacts import apply_promoted_prefilter_to_graph

            apply_promoted_prefilter_to_graph(runtime.settings, graph)
            expected_hash = runtime.settings.pipeline_graph_expected_hash
            if expected_hash is not None and graph.graph_hash != expected_hash:
                raise RuntimeError(
                    "Configured pipeline graph hash mismatch: "
                    f"expected {expected_hash}, got {graph.graph_hash} "
                    f"for {runtime.settings.pipeline_graph_path}"
                )
            if personal_mode:
                # MCP's personal audit must inspect fetched vacancies. Keep the
                # production prefilter gate unchanged; only this ephemeral run
                # records the negative signal and lets the card reach the LLM.
                personal_nodes = []
                for graph_node in graph.spec.nodes:
                    if graph_node.node == "tfidf_logreg_prefilter":
                        graph_node.params["mode"] = "shadow"
                    if graph_node.node == "extraction":
                        graph_node = replace(
                            graph_node,
                            timeout_ms=max(
                                int(graph_node.timeout_ms or 0),
                                int(runtime.settings.openai_timeout_seconds * 1000),
                            ),
                        )
                    personal_nodes.append(graph_node)
                graph = replace(
                    graph,
                    spec=replace(graph.spec, nodes=tuple(personal_nodes)),
                )
            if runtime.settings.llm_backend == "openai":
                relevance_settings = runtime.settings.model_copy(
                    update={"openai_model": runtime.settings.relevance_llm_model}
                )
                relevance_llm = build_llm(relevance_settings)
            else:
                # Non-OpenAI providers are often injected tenant adapters.
                # Rebuilding from Settings silently discarded that runtime
                # provider and made test/custom deployments use heuristics.
                relevance_llm = runtime.llm_provider
            typed_bindings = build_v2_typed_bindings(
                nodes=list(nodes),
                store=runtime.store,
                catalog=effective_catalog,
                relevance_llm=relevance_llm,
                low_threshold=runtime.settings.llm_relevance_low_threshold,
                high_threshold=runtime.settings.llm_relevance_high_threshold,
                max_per_run=runtime.settings.llm_relevance_max_per_run,
                relevance_prompts=relevance_prompts,
                tenant_id=runtime.tenant.tenant_id,
                graph_hash=graph.graph_hash,
                post_accept_llm=runtime.llm_provider,
                post_accept_group_store=runtime.job_group_store,
                post_accept_max_calls=runtime.settings.pipeline_full_extraction_max_calls_per_run,
                post_accept_target_roles=tuple(
                    dict.fromkeys(
                        role
                        for profile in effective_catalog.profiles
                        for role in profile.target_roles
                        if role.strip()
                    )
                )[:60],
                capture_payloads=runtime.settings.tracing_capture_payloads,
                audit_mode=personal_mode,
            )
            executor = build_v2_executor(
                graph,
                nodes=list(nodes),
                sanitize_node=sanitize_node,
                catalog=effective_catalog,
                typed_bindings=typed_bindings,
                tenant_id=runtime.tenant.tenant_id,
                user_id=user_id,
                runtime_resources={
                    "active_store": runtime.store,
                    "active_llm": relevance_llm,
                },
            )
            nodes = [GraphPipelineStage(executor)]
        output_sink, main_sink, review_sink, posting_sink = build_output_sinks(
            runtime.settings,
            max_output_items=personal_max_items if personal_mode else None,
        )
        rejected_counted, rejected_sink = build_rejected_sink(runtime.settings)
        if _snapshot_filter is None and not personal_mode:
            msg = "build_nodes() must return SnapshotFilterNode when run_id is set"
            raise RuntimeError(msg)
        snapshot_filter = _snapshot_filter

        builder = PipelineBuilder()
        builder.sources(effective_sources)
        builder.auth(runtime.auth_provider)
        builder.store(runtime.store)
        builder.stage(sanitize_node)
        if snapshot_filter is not None:
            builder.with_snapshot_filter(
                run_id, tenant_id=runtime.tenant.tenant_id, snapshot_filter=snapshot_filter
            )
        for node in nodes:
            builder.stage(node)
        builder.sink(output_sink)
        builder.with_delivery_targets(build_delivery_targets(runtime.settings, posting_sink))
        builder.with_quarantine_sink(build_quarantine_sink(runtime.settings))
        builder.with_rejected_sink(rejected_sink, counted=rejected_counted)
        builder.set_source_fetch_concurrency(
            resolve_settings_source_fetch_concurrency(
                runtime.settings,
                source_specs=effective_sources,
            )
        )
        builder.set_source_pool_runtime(
            dynamic_enabled=runtime.settings.source_pool_dynamic_enabled,
            soft_deadline_seconds=runtime.settings.source_soft_deadline_seconds,
            hard_deadline_seconds=runtime.settings.source_hard_deadline_seconds,
            overflow_concurrency=runtime.settings.source_overflow_concurrency,
            hard_cancel_grace_seconds=runtime.settings.source_hard_cancel_grace_seconds,
            adaptive_resize=runtime.settings.source_pool_adaptive_resize,
            concurrency_max=runtime.settings.source_fetch_concurrency_max,
        )
        builder.set_pipeline_item_concurrency(
            resolve_settings_pipeline_item_concurrency(
                runtime.settings,
                source_specs=effective_sources,
                source_count=len(effective_sources),
            )
        )
        builder.set_default_max_items(runtime.settings.pipeline_max_items_per_run)
        builder.set_summary_context(
            output_sink=main_sink,
            review_sink=review_sink,
            posting_sink=posting_sink,
            job_group_store=runtime.job_group_store,
            profile_name=effective_catalog.catalog_name,
            output_path=runtime.settings.output_path,
        )
        return builder, snapshot_filter

    def _apply_runtime_sources(self, runtime: TenantRuntime) -> None:
        effective_sources = _merge_effective_sources(
            runtime.base_sources,
            runtime.runtime_sources,
            runtime.disabled_source_ids,
        )
        runtime.builder.sources(effective_sources)
        runtime.tenant = runtime.tenant.model_copy(update={"sources": effective_sources})

    async def _prepare_effective_source(
        self,
        runtime: TenantRuntime,
        spec: SourceSpec,
        *,
        assessment_service: Any,
        health: SourceHealth | None = None,
        now: datetime,
    ) -> tuple[str, SourceSpec]:
        sid = source_spec_identifier(spec)
        await assessment_service.assess_and_store(
            spec, runtime.store, ttl_days=runtime.settings.source_assessment_ttl_days
        )
        assessment = await load_source_assessment(runtime.store, sid)
        bootstrap_completed_at = await self._get_source_bootstrap_completed_at(runtime, sid)
        last_started_at = _parse_optional_datetime(
            health.last_started_at if health is not None else None
        )
        last_successful_run_at = _parse_optional_datetime(
            health.last_success_at if health is not None else None
        )
        effective_spec = _apply_runtime_fetch_window(
            spec,
            assessment=assessment,
            bootstrap_completed_at=bootstrap_completed_at,
            last_started_at=last_started_at,
            last_successful_run_at=last_successful_run_at,
            interval_seconds=_resolve_source_interval_seconds(runtime, spec),
            now=now,
        )
        return sid, effective_spec

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

        assessment_service = create_source_assessment_service()
        await assessment_service.assess_and_store(
            spec, runtime.store, ttl_days=runtime.settings.source_assessment_ttl_days
        )

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
            try:
                await runtime.store.save_runtime_source(record)
                await runtime.store.set_source_disabled(source_id, False)
            except Exception:
                with suppress(Exception):
                    await runtime.store.delete_runtime_source(source_id)
                await self._reload_runtime_sources(runtime)
                raise
            runtime.runtime_sources[source_id] = record
            runtime.disabled_source_ids.discard(source_id)

        self._apply_runtime_sources(runtime)
        return await _attach_source_assessment(
            runtime,
            _build_source_listing_payload(
                spec,
                origin="runtime",
                enabled=True,
                health=None,
            ),
        )

    async def disable_source(self, tenant_id: str, source_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        base_ids = {source_spec_identifier(item) for item in runtime.base_sources}
        runtime_record = runtime.runtime_sources.get(source_id)
        if source_id not in base_ids and runtime_record is None:
            msg = f"Unknown source_id: {source_id}"
            raise KeyError(msg)

        previous_runtime_record = runtime_record
        if runtime_record is not None:
            runtime_record = runtime_record.model_copy(update={"enabled": False})
        try:
            if runtime_record is not None:
                await runtime.store.save_runtime_source(runtime_record)
            await runtime.store.set_source_disabled(source_id, True)
        except Exception:
            if previous_runtime_record is not None:
                with suppress(Exception):
                    await runtime.store.save_runtime_source(previous_runtime_record)
            await self._reload_runtime_sources(runtime)
            raise
        if runtime_record is not None:
            runtime.runtime_sources[source_id] = runtime_record
        runtime.disabled_source_ids.add(source_id)
        self._apply_runtime_sources(runtime)
        payloads = await self.list_sources(tenant_id)
        for payload in payloads:
            if payload["source_id"] == source_id:
                return payload
        msg = f"Failed to disable source: {source_id}"
        raise RuntimeError(msg)

    async def update_source(
        self,
        tenant_id: str,
        source_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        unknown = [key for key in patch if key not in {"enabled", "limit", "url"}]
        if unknown:
            return {
                "error": "invalid_arguments",
                "message": "patch may only include enabled, limit, and/or url",
                "unknown_keys": unknown,
                "source_id": source_id,
            }
        if "enabled" in patch and not isinstance(patch["enabled"], bool):
            return {
                "error": "invalid_arguments",
                "message": "enabled must be a boolean",
                "source_id": source_id,
            }
        if "limit" in patch and patch["limit"] is not None and not isinstance(patch["limit"], int):
            return {
                "error": "invalid_arguments",
                "message": "limit must be an int or null",
                "source_id": source_id,
            }
        if "url" in patch and not isinstance(patch["url"], str):
            return {
                "error": "invalid_arguments",
                "message": "url must be a string",
                "source_id": source_id,
            }

        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        listed = await self.list_sources(tenant_id)
        listed_item = next((item for item in listed if item.get("source_id") == source_id), None)
        base_ids = {source_spec_identifier(item) for item in runtime.base_sources}
        runtime_record = runtime.runtime_sources.get(source_id)
        is_config = source_id in base_ids or (
            listed_item is not None and listed_item.get("origin") == "config"
        )
        if not is_config and runtime_record is None:
            msg = f"Unknown source_id: {source_id}"
            raise KeyError(msg)

        if is_config and "limit" in patch:
            return {
                "status": "unsupported",
                "error": "config_limit_not_updatable",
                "source_id": source_id,
                "hint": "do not rewrite YAML; add a runtime source to change limit",
            }
        if is_config and "url" in patch:
            return {
                "status": "unsupported",
                "error": "config_url_not_updatable",
                "source_id": source_id,
                "hint": "update the tenant config instead",
            }
        if is_config:
            target_id = _canonical_base_source_id(runtime, source_id, listed_item) or source_id
            if patch.get("enabled") is False:
                try:
                    return await self.disable_source(tenant_id, target_id)
                except RuntimeError:
                    payload = _listing_payload_for_source(
                        await self.list_sources(tenant_id),
                        target_id,
                        runtime,
                    )
                    if payload is None:
                        payload = listed_item
                    if payload is not None:
                        return payload
                    raise
            if patch.get("enabled") is True:
                await runtime.store.set_source_disabled(target_id, False)
                runtime.disabled_source_ids.discard(target_id)
                self._apply_runtime_sources(runtime)
            payloads = await self.list_sources(tenant_id)
            for payload in payloads:
                if payload["source_id"] in {source_id, target_id}:
                    return payload
            msg = f"Failed to update source: {source_id}"
            raise RuntimeError(msg)

        if runtime_record is None:
            msg = f"Unknown source_id: {source_id}"
            raise KeyError(msg)
        new_spec = runtime_record.spec
        if "limit" in patch:
            fields = getattr(type(new_spec), "model_fields", {})
            if "limit" not in fields:
                return {
                    "status": "unsupported",
                    "error": "limit_not_supported",
                    "source_id": source_id,
                    "hint": "this source spec has no limit field",
                }
            new_spec = new_spec.model_copy(update={"limit": patch["limit"]})
        if "url" in patch:
            fields = getattr(type(new_spec), "model_fields", {})
            if "url" not in fields:
                return {
                    "status": "unsupported",
                    "error": "url_not_supported",
                    "source_id": source_id,
                    "hint": "this source spec has no url field",
                }
            try:
                new_spec = type(new_spec).model_validate(
                    {**new_spec.model_dump(mode="python"), "url": patch["url"]}
                )
            except ValueError as exc:
                return {
                    "error": "invalid_arguments",
                    "message": str(exc),
                    "source_id": source_id,
                }
        enabled = runtime_record.enabled if "enabled" not in patch else bool(patch["enabled"])
        updated = runtime_record.model_copy(update={"spec": new_spec, "enabled": enabled})
        previous = runtime_record
        try:
            await runtime.store.save_runtime_source(updated)
            if "enabled" in patch:
                await runtime.store.set_source_disabled(source_id, not enabled)
        except Exception:
            with suppress(Exception):
                await runtime.store.save_runtime_source(previous)
            await self._reload_runtime_sources(runtime)
            raise
        runtime.runtime_sources[source_id] = updated
        if enabled:
            runtime.disabled_source_ids.discard(source_id)
        else:
            runtime.disabled_source_ids.add(source_id)
        self._apply_runtime_sources(runtime)
        payloads = await self.list_sources(tenant_id)
        for payload in payloads:
            if payload["source_id"] == source_id:
                return payload
        msg = f"Failed to update source: {source_id}"
        raise RuntimeError(msg)

    async def remove_source(self, tenant_id: str, source_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        listed = await self.list_sources(tenant_id)
        listed_item = next((item for item in listed if item.get("source_id") == source_id), None)
        base_ids = {source_spec_identifier(item) for item in runtime.base_sources}
        if source_id in base_ids or (
            listed_item is not None and listed_item.get("origin") == "config"
        ):
            return {
                "status": "unsupported",
                "error": "config_source_not_deletable",
                "source_id": source_id,
                "hint": "use disable_source for config/base sources",
            }
        runtime_record = runtime.runtime_sources.get(source_id)
        if runtime_record is None:
            msg = f"Unknown source_id: {source_id}"
            raise KeyError(msg)
        previous = runtime_record
        try:
            await runtime.store.delete_runtime_source(source_id)
        except Exception:
            with suppress(Exception):
                await runtime.store.save_runtime_source(previous)
            await self._reload_runtime_sources(runtime)
            raise
        runtime.runtime_sources.pop(source_id, None)
        runtime.disabled_source_ids.discard(source_id)
        self._apply_runtime_sources(runtime)
        return {
            "status": "removed",
            "source_id": source_id,
            "origin": previous.origin,
        }

    async def clear_sources(self, tenant_id: str) -> None:
        """Clear all runtime sources and disable base config sources."""
        runtime = self.get_runtime(tenant_id)
        await self._ensure_runtime_sources_loaded(runtime)
        previous_runtime_records = list(runtime.runtime_sources.values())
        previous_disabled_source_ids = set(runtime.disabled_source_ids)
        base_source_ids = [source_spec_identifier(spec) for spec in runtime.base_sources]

        try:
            await runtime.store.clear_runtime_sources()
            for source_id in base_source_ids:
                await runtime.store.set_source_disabled(source_id, True)
        except Exception:
            for record in previous_runtime_records:
                with suppress(Exception):
                    await runtime.store.save_runtime_source(record)
            for source_id in base_source_ids:
                with suppress(Exception):
                    await runtime.store.set_source_disabled(
                        source_id,
                        source_id in previous_disabled_source_ids,
                    )
            await self._reload_runtime_sources(runtime)
            raise

        runtime.runtime_sources.clear()
        for source_id in base_source_ids:
            runtime.disabled_source_ids.add(source_id)

        self._apply_runtime_sources(runtime)

    async def run_tenant(
        self,
        tenant_id: str,
        *,
        max_items: int | None = None,
        user_id: str | None = None,
        source_ids: Sequence[str] | None = None,
        bypass_override: str | None = None,
        parser_override: str | None = None,
        ignore_schedule_gates: bool = False,
        operator_session_id: str | None = None,
        personal_mode: bool = False,
    ) -> RunSummary:
        """Run acquisition and policy under one correlated trace/run id."""
        run_id = uuid.uuid4().hex
        context_tokens = bind_contextvars(tenant_id=tenant_id, source_run_id=run_id)
        tracer = trace.get_tracer("job_ftch.tenant_runner")
        attach_token = None
        borrowed = False
        try:
            with tracer.start_as_current_span("ingest.run") as span:
                span.set_attribute("job_ftch.source_run_id", run_id)
                span.set_attribute("job_ftch.tenant_id", tenant_id)
                runtime = self.get_runtime(tenant_id)
                if operator_session_id:
                    from job_ftch.infrastructure.sources.browser_utils import (
                        attach_operator_page,
                    )

                    attached = await self._session_service().borrow(
                        operator_session_id,
                        tenant_id,
                    )
                    if isinstance(attached, dict):
                        raise OperatorSessionAttachError(attached)
                    borrowed = True
                    attach_token = attach_operator_page(attached.page)
                try:
                    async with _tenant_run_lock(runtime.settings, tenant_id):
                        summary = await self._run_tenant_bound(
                            tenant_id,
                            run_id=run_id,
                            max_items=max_items,
                            user_id=user_id,
                            source_ids=source_ids,
                            bypass_override=bypass_override,
                            parser_override=parser_override,
                            ignore_schedule_gates=ignore_schedule_gates,
                            lock_already_held=True,
                            personal_mode=personal_mode,
                        )
                except TenantRunAlreadyActiveError:
                    logger.info("tenant_run_skipped_already_active", tenant_id=tenant_id)
                    summary = RunSummary()
                    summary.tenant_id = tenant_id
                    summary.source_run_id = run_id
                    summary.finished_at = datetime.now(UTC)
                    summary.skipped_already_active = True
                except TenantRunLockError as exc:
                    logger.error(
                        "tenant_run_skipped_lock_error", tenant_id=tenant_id, error=str(exc)
                    )
                    summary = RunSummary()
                    summary.tenant_id = tenant_id
                    summary.source_run_id = run_id
                    summary.finished_at = datetime.now(UTC)
                    summary.failed = 1
                    summary.drop_reasons["lock_error"] = 1
                span.set_attribute(
                    "job_ftch.skipped_already_active", summary.skipped_already_active
                )
                span.set_attribute("job_ftch.routing_accepted", summary.emitted)
                span.set_attribute("job_ftch.failed", summary.failed)
                return summary
        finally:
            if attach_token is not None:
                from job_ftch.infrastructure.sources.browser_utils import (
                    reset_operator_page,
                )

                reset_operator_page(attach_token)
            if borrowed and operator_session_id:
                await self._session_service().release(operator_session_id)
            reset_contextvars(**context_tokens)

    async def _run_tenant_bound(
        self,
        tenant_id: str,
        *,
        run_id: str,
        max_items: int | None = None,
        user_id: str | None = None,
        source_ids: Sequence[str] | None = None,
        bypass_override: str | None = None,
        parser_override: str | None = None,
        ignore_schedule_gates: bool = False,
        lock_already_held: bool = False,
        personal_mode: bool = False,
    ) -> RunSummary:
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
        requested_source_ids = set(source_ids or ())
        if requested_source_ids:
            available_source_ids = {
                source_spec_identifier(spec)
                for spec in all_specs
                if source_spec_identifier(spec) not in runtime.disabled_source_ids
            }
            missing_source_ids = requested_source_ids - available_source_ids
            if missing_source_ids:
                raise ValueError(
                    f"Requested sources are missing or disabled: {sorted(missing_source_ids)}"
                )
        # Filter for unique specs by ID (handling overlaps between base and runtime)
        seen_specs: set[str] = set()
        unique_specs: list[SourceSpec] = []
        for spec in all_specs:
            sid = source_spec_identifier(spec)
            if (
                sid in seen_specs
                or sid in runtime.disabled_source_ids
                or (requested_source_ids and sid not in requested_source_ids)
            ):
                continue
            seen_specs.add(sid)
            unique_specs.append(spec)

        if bypass_override or parser_override:
            pinned_specs: list[SourceSpec] = []
            for spec in unique_specs:
                current = spec
                if bypass_override:
                    copier = getattr(current, "model_copy", None)
                    current = (
                        current.model_copy(update={"bypass": bypass_override})
                        if callable(copier)
                        else current
                    )
                if parser_override:
                    current = _pin_parser(current, parser_override)
                pinned_specs.append(current)
            unique_specs = pinned_specs

        assessment_service = create_source_assessment_service()
        runnable_specs: list[SourceSpec] = []
        planned_source_ids: list[str] = []
        for spec in unique_specs:
            sid = source_spec_identifier(spec)
            health = health_map.get(sid)

            # Task 3: Rate Limit
            if (not ignore_schedule_gates) and health and health.last_run_at:
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

            # Sources stay enabled after failures. Clear legacy auto-pause rows
            # so repaired adapters are exercised on the next scheduled run.
            if (not ignore_schedule_gates) and health and health.paused:
                logger.info("source_auto_resumed", source_id=sid)
                health.paused = False
                health.skipped_runs = 0
                health.status = "failing"
                await runtime.store.save_source_health(sid, health)

            runnable_specs.append(spec)

        if runnable_specs:
            source_preparation_concurrency = resolve_settings_source_preparation_concurrency(
                runtime.settings,
                source_specs=runnable_specs,
            )
            await runtime.store.set_run_state(
                "pipeline.source_preparation_concurrency",
                str(source_preparation_concurrency),
            )
            semaphore = asyncio.Semaphore(max(source_preparation_concurrency, 1))

            async def _prepare(spec: SourceSpec) -> tuple[str, SourceSpec]:
                async with semaphore:
                    sid = source_spec_identifier(spec)
                    prepared_sid, prepared_spec = await self._prepare_effective_source(
                        runtime,
                        spec,
                        assessment_service=assessment_service,
                        health=health_map.get(sid),
                        now=now,
                    )
                    from job_ftch.domain.source_spec import CareerSiteSpec

                    if personal_mode and isinstance(prepared_spec, CareerSiteSpec):
                        # A manual MCP audit is source/run scoped; a previous
                        # bootstrap cutoff must not hide live detail cards. Its
                        # item cap belongs at the source frontier; using the
                        # pipeline work budget would spend the cap on a parent
                        # before its CandidateSpan can reach extraction.
                        personal_limit = int(max_items) if max_items is not None else None
                        prepared_spec = prepared_spec.model_copy(
                            update={
                                "freshness_cutoff_utc": None,
                                **({"limit": personal_limit} if personal_limit else {}),
                            }
                        )
                    return prepared_sid, prepared_spec

            prepared = await asyncio.gather(*(_prepare(spec) for spec in runnable_specs))
            for sid, effective_spec in prepared:
                effective_sources.append(effective_spec)
                planned_source_ids.append(sid)

        if not effective_sources:
            # Return an empty summary if no sources are runnable this cycle
            summary = RunSummary()
            summary.tenant_id = tenant_id
            summary.source_run_id = run_id
            summary.finish()
            return summary

        # Task 4: Jitter before dispatching
        if runtime.settings.scheduler_jitter_seconds > 0:
            jitter = random.uniform(0.0, runtime.settings.scheduler_jitter_seconds)
            await asyncio.sleep(jitter)

        try:
            lock_context = (
                nullcontext()
                if lock_already_held
                else _tenant_run_lock(runtime.settings, tenant_id)
            )
            async with lock_context:
                try:
                    await runtime.store.set_run_state("pipeline.run_summary", "")
                except Exception as exc:
                    logger.warning(
                        "tenant_run_summary_state_clear_failed",
                        tenant_id=tenant_id,
                        error=str(exc),
                        exc_info=True,
                    )
                with collect_llm_usage() as usage:
                    catalog, relevance_prompts = await self._build_runtime_catalog(
                        runtime, user_id=user_id
                    )
                    builder_kwargs: dict[str, Any] = {
                        "runtime": runtime,
                        "effective_sources": effective_sources,
                        "catalog": catalog,
                        "run_id": run_id,
                        "user_id": user_id,
                        "relevance_prompts": relevance_prompts,
                    }
                    if personal_mode:
                        builder_kwargs["personal_mode"] = True
                        builder_kwargs["personal_max_items"] = max_items
                    builder, snapshot_filter = await self._build_runtime_builder(**builder_kwargs)
                    summary = await builder.run_async(
                        max_items=None if personal_mode else max_items
                    )
                summary.llm_usage_requests = usage.requests
                summary.llm_tokens_in = usage.tokens_in
                summary.llm_cached_tokens_in = usage.cached_tokens_in
                summary.llm_tokens_out = usage.tokens_out
                summary.llm_latency_ms = usage.latency_ms
                summary.llm_cost_usd = usage.cost_usd
                summary.llm_cost_is_complete = usage.cost_is_complete
                summary.llm_cost_pricing_version = pricing_version()
                summary.llm_cost_unknown_models = sorted(usage.unknown_pricing_models)
        except TenantRunAlreadyActiveError:
            logger.info("tenant_run_skipped_already_active", tenant_id=tenant_id)
            summary = RunSummary()
            summary.tenant_id = tenant_id
            summary.source_run_id = run_id
            summary.finished_at = datetime.now(UTC)
            return summary
        except TenantRunLockError as exc:
            logger.error("tenant_run_skipped_lock_error", tenant_id=tenant_id, error=str(exc))
            summary = RunSummary()
            summary.tenant_id = tenant_id
            summary.source_run_id = run_id
            summary.finished_at = datetime.now(UTC)
            summary.failed = 1
            summary.drop_reasons["lock_error"] = 1
            return summary

        summary.tenant_id = tenant_id
        from job_ftch.infrastructure.observability.openobserve import record_run_metrics
        from job_ftch.infrastructure.observability.otel_setup import record_final_run_trace

        record_final_run_trace(summary)
        # Emit the run-terminal log before the exporter flush. record_run_metrics
        # ends by flushing both the log and meter providers, so a log written
        # afterwards stays queued in the batch processor and a verifier querying
        # by run id right after the run finds metrics but no operational log.
        logger.info(
            "tenant_run_complete",
            tenant_id=tenant_id,
            run_id=summary.source_run_id,
            graph_hash=summary.graph_hash,
            fetched=summary.fetched,
            extracted=summary.extracted,
            accepted=summary.emitted,
            review=summary.review,
            rejected=summary.rejected,
            deferred=summary.deferred,
            failed=summary.failed,
            source_partial=summary.source_partial,
            source_failures=summary.source_failures,
            source_outcomes=summary.source_outcomes,
            llm_requests=summary.llm_usage_requests,
            llm_cost_usd=summary.llm_cost_usd,
            llm_cost_is_complete=summary.llm_cost_is_complete,
            llm_cost_unknown_models=summary.llm_cost_unknown_models,
        )
        record_run_metrics(summary)
        try:
            await runtime.store.set_run_state(
                "pipeline.run_summary",
                json.dumps(
                    summary.as_dict(), default=_json_default, ensure_ascii=False, sort_keys=True
                ),
            )
        except Exception as exc:
            logger.warning(
                "tenant_run_summary_state_persist_failed",
                tenant_id=tenant_id,
                error=str(exc),
                exc_info=True,
            )
        try:
            await runtime.store.save_run_summary(summary)
        except Exception as exc:
            logger.warning(
                "tenant_run_history_persist_failed",
                tenant_id=tenant_id,
                run_id=summary.source_run_id,
                error=str(exc),
                exc_info=True,
            )
        try:
            await self._update_source_health(runtime, summary)
        except Exception as exc:
            logger.warning(
                "tenant_source_health_update_failed",
                tenant_id=tenant_id,
                run_id=summary.source_run_id,
                error=str(exc),
                exc_info=True,
            )
        try:
            await self.refresh_runtime_state_metrics(tenant_id, summary)
        except Exception as exc:
            logger.warning(
                "tenant_runtime_state_metrics_failed",
                tenant_id=tenant_id,
                run_id=summary.source_run_id,
                error=str(exc),
                exc_info=True,
            )
        completed_at = summary.finished_at or datetime.now(UTC)
        completed_source_ids = {
            outcome.get("source_id")
            for outcome in summary.source_outcomes
            if outcome.get("completion_state") == "completed" and outcome.get("source_id")
        }
        for source_id in planned_source_ids:
            stats = summary.by_source_id.get(source_id)
            if (
                stats is not None
                and stats.failed == 0
                and source_id in completed_source_ids
                and await self._get_source_bootstrap_completed_at(runtime, source_id) is None
            ):
                try:
                    await self._mark_source_bootstrap_completed(runtime, source_id, completed_at)
                except Exception as exc:
                    logger.warning(
                        "tenant_source_bootstrap_mark_failed",
                        tenant_id=tenant_id,
                        source_id=source_id,
                        error=str(exc),
                        exc_info=True,
                    )
        return summary

    async def _update_source_health(self, runtime: TenantRuntime, summary: RunSummary) -> None:
        finished_at = summary.finished_at or datetime.now()
        started_at = summary.started_at
        failure_by_id = {
            item.get("source_id", ""): item
            for item in getattr(summary, "source_failures", [])
            if item.get("source_id")
        }
        eviction_by_id = {
            item.get("source_id", ""): item
            for item in getattr(summary, "source_evictions", [])
            if item.get("source_id")
        }
        for source_id, stats in summary.by_source_id.items():
            source_kind, _, source_name = source_id.partition(":")
            previous = await runtime.store.get_source_health(source_id)
            payload = _update_source_health_payload(
                previous,
                source_id=source_id,
                source_kind=source_kind,
                source_name=source_name,
                stats=stats,
                started_at=started_at,
                finished_at=finished_at,
                drift_ratio_threshold=runtime.settings.source_health_drift_ratio,
                min_baseline_threshold=runtime.settings.source_health_min_baseline,
            )
            failure_payload = failure_by_id.get(source_id)
            if failure_payload is not None:
                # Prefer an allowlisted parser/health code already present on
                # the failure payload or embedded in free text (e.g.
                # "layout_changed: …" from GetmatchIngestError). Fall back to
                # the generic source_fetch_failed only when nothing specific
                # is available — no new schema fields.
                from job_ftch.application.public_source_registry import (
                    extract_public_failure_code,
                )

                raw_error = failure_payload.get("error")
                raw_kind = failure_payload.get("error_kind") or failure_payload.get(
                    "last_error_kind"
                )
                error_kind = (
                    extract_public_failure_code(raw_kind)
                    or extract_public_failure_code(raw_error)
                    or "source_fetch_failed"
                )
                payload = payload.model_copy(
                    update={
                        "last_error": raw_error,
                        "last_error_kind": error_kind,
                        "last_error_at": finished_at.isoformat(),
                    }
                )
            elif stats.failed == 0 and payload.last_error is not None:
                payload = payload.model_copy(
                    update={"last_error": None, "last_error_kind": None, "last_error_at": None}
                )
            eviction_payload = eviction_by_id.get(source_id)
            if eviction_payload is not None:
                eviction_streak = (previous.eviction_streak if previous else 0) + 1
                should_pause = (
                    payload.paused
                    or eviction_streak >= runtime.settings.source_eviction_pause_threshold
                )
                payload = payload.model_copy(
                    update={
                        "last_eviction_at": finished_at.isoformat(),
                        "eviction_streak": eviction_streak,
                        "last_eviction_kind": eviction_payload.get("eviction_kind"),
                        "paused": should_pause,
                        "status": "paused" if should_pause else payload.status,
                    }
                )
                if should_pause and not (previous.paused if previous else False):
                    logger.info(
                        "source_eviction_paused",
                        tenant_id=runtime.tenant.tenant_id,
                        source_id=source_id,
                        eviction_streak=eviction_streak,
                        eviction_kind=eviction_payload.get("eviction_kind"),
                    )
            elif payload.eviction_streak != 0:
                payload = payload.model_copy(update={"eviction_streak": 0})
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

    async def refresh_runtime_state_metrics(self, tenant_id: str, summary: RunSummary) -> None:
        await self._record_runtime_state_metrics(self.get_runtime(tenant_id), summary)

    async def _record_runtime_state_metrics(
        self,
        runtime: TenantRuntime,
        summary: RunSummary,
    ) -> None:
        from job_ftch.infrastructure.observability.openobserve import record_runtime_state_metrics

        scheduler_keys = (
            "bot_scheduler:last_attempt_at",
            "bot_scheduler:last_success_at",
            "bot_scheduler:last_publish_success_at",
            "bot_scheduler:pending_publish_since",
            "bot_scheduler:last_publish_error",
            "bot_scheduler:last_publish_sent",
        )
        scheduler_state = {key: await _read_runtime_state(runtime, key) for key in scheduler_keys}
        record_runtime_state_metrics(
            summary,
            source_health=await runtime.store.list_source_health(),
            scheduler_state=scheduler_state,
        )

    async def run_all(
        self,
        *,
        concurrency: int = 4,
        max_items: int | None = None,
        user_id: str | None = None,
    ) -> list[RunSummary]:
        semaphore = asyncio.Semaphore(max(concurrency, 1))

        async def run_one(tenant_id: str) -> RunSummary | None:
            async with semaphore:
                try:
                    return await self.run_tenant(
                        tenant_id,
                        max_items=max_items,
                        user_id=user_id,
                    )
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
                await _attach_source_assessment(
                    runtime,
                    _build_source_listing_payload(
                        spec,
                        origin="config",
                        enabled=source_id not in runtime.disabled_source_ids,
                        health=health_by_id.get(source_id) or health_by_name.get(source_name),
                    ),
                )
            )
        config_source_ids = {source_spec_identifier(spec) for spec in runtime.base_sources}
        for source_id in sorted(runtime.runtime_sources):
            if source_id in config_source_ids:
                continue
            record = runtime.runtime_sources[source_id]
            source_name = source_spec_name(record.spec)
            payloads.append(
                await _attach_source_assessment(
                    runtime,
                    _build_source_listing_payload(
                        record.spec,
                        origin=record.origin,
                        enabled=record.enabled and source_id not in runtime.disabled_source_ids,
                        health=health_by_id.get(source_id) or health_by_name.get(source_name),
                    ),
                )
            )
        payloads.sort(key=lambda item: str(item["source_id"]))
        return payloads

    async def list_public_sources(
        self,
        tenant_id: str,
        *,
        allowlist: frozenset[str] | None = None,
    ) -> PublicSourceRegistry:
        """Return a public-safe source registry built from runtime store state.

        Uses the same ``list_sources`` path as bot/API/MCP. Never reads fixtures.
        Tenants outside the allowlist receive an explicit error envelope.
        """
        from job_ftch.application.public_source_registry import list_public_sources_for_runner

        if tenant_id in self._runtimes:
            runtime = self.get_runtime(tenant_id)
            await self._ensure_runtime_sources_loaded(runtime)
            # Bot and public API are separate processes; refresh overlays written
            # by the bot before serving the cached public projection.
            await self._reload_runtime_sources(runtime)
        return await list_public_sources_for_runner(self, tenant_id, allowlist=allowlist)

    def _settings_for_inventory(self, tenant_id: str | None = None) -> Settings:
        if tenant_id is not None and tenant_id in self._runtimes:
            return self._runtimes[tenant_id].settings
        if self._runtimes:
            return next(iter(self._runtimes.values())).settings
        return get_settings()

    def default_escalation_ladder(self) -> list[str]:
        """Registered adaptive order (noop → cloak). Composition-root only."""
        from job_ftch.application.registry import list_bypass_capabilities
        from job_ftch.infrastructure.bypass.adaptive import DEFAULT_TIER_ORDER

        try:
            registered = set(list_bypass_capabilities())
        except Exception:
            registered = set()
        names = [name for name in DEFAULT_TIER_ORDER if not registered or name in registered]
        return names or ["noop"]

    def list_browser_capabilities(
        self,
        tenant_id: str | None = None,
    ) -> BrowserCapabilityInventory:
        """Return a read-only browser/bypass capability inventory.

        Reuses registry capabilities and settings budgets. Does not start
        browsers, open sessions, or expose secret values.
        """
        from job_ftch.application.browser_capability_inventory import (
            build_browser_capability_inventory,
        )

        return build_browser_capability_inventory(self._settings_for_inventory(tenant_id))

    async def explain_browser_route(
        self,
        tenant_id: str | None = None,
        source_id: str | None = None,
        *,
        bypass: str | None = None,
    ) -> RoutePlanExplanation:
        """Explain route selection/unavailability for a source without executing it."""
        from job_ftch.application.browser_capability_inventory import explain_route_plan

        source_payload: dict[str, Any] | None = None
        if tenant_id and source_id:
            sources = await self.list_sources(tenant_id)
            for item in sources:
                if str(item.get("source_id") or "") == source_id:
                    source_payload = item
                    break
            if source_payload is None:
                return RoutePlanExplanation(
                    generated_at=datetime.now(UTC),
                    source_id=source_id,
                    requested_bypass=bypass,
                    error="source not found",
                )
        elif tenant_id and bypass is None:
            # Tenant-scoped default plan without a specific source.
            source_payload = {"kind": "career_site"}
        return explain_route_plan(
            settings=self._settings_for_inventory(tenant_id),
            source=source_payload,
            source_id=source_id,
            requested_bypass=bypass,
        )

    async def probe_browser_listing(
        self,
        tenant_id: str | None,
        *,
        url: str,
        engine: str = "auto",
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
        probe: str = "listing",
        solve: str = "none",
        url_filter: Any = None,
    ) -> dict[str, Any]:
        """Open one ephemeral page. Does not ingest or keep a session."""
        from job_ftch.infrastructure.browser_probe import LiveBrowserSessionProbe

        probe_kind = (probe or "listing").strip().lower()
        live = LiveBrowserSessionProbe(self._settings_for_inventory(tenant_id))
        return await live.probe(
            url=url,
            engine=engine,
            probe=probe_kind,
            headed=headed,
            max_items=max_items,
            bypass_config=bypass_config,
            solve=solve,
            url_filter=url_filter,
        )

    def _session_service(self) -> Any:
        if self._operator_sessions is None:
            from job_ftch.infrastructure.browser_session import OperatorBrowserSessionService

            self._operator_sessions = OperatorBrowserSessionService()
        return self._operator_sessions

    def _session_call(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        return {
            "status": "error",
            "error": "invalid_session_payload",
            "ok": False,
            "executed": False,
        }

    async def open_operator_browser_session(
        self,
        tenant_id: str,
        *,
        url: str,
        engine: str = "auto",
        headed: bool = False,
        bypass_config: dict[str, Any] | None = None,
        manual_challenge: bool = False,
        profile: str = "ephemeral",
    ) -> dict[str, Any]:
        return self._session_call(
            await self._session_service().open(
                tenant_id=tenant_id,
                url=url,
                engine=engine,
                headed=headed,
                bypass_config=bypass_config,
                manual_challenge=manual_challenge,
                profile=profile,
            )
        )

    async def get_operator_browser_session(self, session_id: str) -> dict[str, Any]:
        return self._session_call(await self._session_service().get(session_id))

    async def continue_operator_browser_session(
        self,
        session_id: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        return self._session_call(
            await self._session_service().continue_session(session_id, instruction)
        )

    async def capture_operator_browser_artifact(
        self,
        session_id: str,
        artifact_type: str,
    ) -> dict[str, Any]:
        return self._session_call(await self._session_service().capture(session_id, artifact_type))

    async def close_operator_browser_session(self, session_id: str) -> dict[str, Any]:
        return self._session_call(await self._session_service().close(session_id))

    async def create_search_session(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        profile_id: str | None = None,
        source_scope: Sequence[str] | None = None,
        max_items: int | None = None,
        max_sources: int | None = None,
        result_limit: int = 20,
        deadline_seconds: float | None = None,
    ) -> Any:
        """Create a resume-driven search session for high-level agent workflows."""
        from job_ftch.application.search_session import create_search_session
        from job_ftch.domain.search_session import SearchSessionBudgets

        return await create_search_session(
            self,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
            source_scope=source_scope,
            budgets=SearchSessionBudgets(
                max_items=max_items,
                max_sources=max_sources,
                result_limit=result_limit,
                deadline_seconds=deadline_seconds,
            ),
        )

    async def plan_source_routes(self, session_id: str) -> Any:
        """Plan per-source browser/bypass routes for a search session."""
        from job_ftch.application.search_session import plan_source_routes

        return await plan_source_routes(self, session_id)

    async def approve_search_session(
        self,
        session_id: str,
        *,
        approved_source_ids: Sequence[str] | None = None,
        approved_capability_ids: Sequence[str] | None = None,
        approve_all_sensitive: bool = False,
        note: str | None = None,
    ) -> Any:
        """Record approvals for sensitive routes before running a session."""
        from job_ftch.application.search_session import approve_search_session

        return await approve_search_session(
            self,
            session_id,
            approved_source_ids=approved_source_ids,
            approved_capability_ids=approved_capability_ids,
            approve_all_sensitive=approve_all_sensitive,
            note=note,
        )

    async def run_search_session(
        self,
        session_id: str,
        *,
        skip_pipeline: bool = False,
    ) -> Any:
        """Run an approved search session via existing tenant pipeline/search APIs."""
        from job_ftch.application.search_session import run_search_session

        return await run_search_session(self, session_id, skip_pipeline=skip_pipeline)

    async def get_search_session_status(self, session_id: str) -> Any:
        """Return search session status and route plan state."""
        from job_ftch.application.search_session import get_search_session_status

        return await get_search_session_status(self, session_id)

    async def list_search_results(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[Any]:
        """List ranked job result refs for a search session."""
        from job_ftch.application.search_session import list_search_results

        return await list_search_results(self, session_id, limit=limit)

    async def explain_search_session(
        self,
        session_id: str,
        *,
        source_id: str | None = None,
        job_id: str | None = None,
    ) -> Any:
        """Explain rejected/degraded sources or non-selected jobs in a session."""
        from job_ftch.application.search_session import explain_rejected_or_degraded

        return await explain_rejected_or_degraded(
            self,
            session_id,
            source_id=source_id,
            job_id=job_id,
        )

    async def cancel_search_session(self, session_id: str) -> Any:
        """Cancel a search session (cooperative if a run is in flight)."""
        from job_ftch.application.search_session import cancel_search_session

        return await cancel_search_session(self, session_id)

    async def ingest_resume(
        self,
        tenant_id: str,
        *,
        user_id: str,
        resume_text: str,
        profile_id: str | None = None,
        activate: bool = True,
    ) -> ManagedCandidateProfile:
        """Ingest resume text into a managed candidate profile for search sessions."""
        from job_ftch.application.search_session import ingest_resume

        return await ingest_resume(
            self,
            tenant_id=tenant_id,
            user_id=user_id,
            resume_text=resume_text,
            profile_id=profile_id,
            activate=activate,
        )

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

    async def save_and_activate_candidate_profile(
        self,
        tenant_id: str,
        record: ManagedCandidateProfile,
    ) -> dict[str, Any]:
        """Persist the profile AND mark it active in one logical step.

        The bot adapter should call this instead of the
        ``save_candidate_profile`` + ``set_active_candidate_profile``
        pair. Two-call patterns race on the first ``/run`` — see the
        docstring on
        :meth:`TenantStore.save_and_activate_candidate_profile`.
        """
        runtime = self.get_runtime(tenant_id)
        await runtime.store.save_and_activate_candidate_profile(record)
        profiles = await self.list_candidate_profiles(tenant_id, record.user_id)
        for profile in profiles:
            if profile["profile_id"] == record.profile_id:
                return profile
        msg = f"Failed to persist and activate candidate profile: {record.profile_id}"
        raise RuntimeError(msg)

    async def get_candidate_profile(
        self,
        tenant_id: str,
        user_id: str,
        profile_id: str,
    ) -> ManagedCandidateProfile | None:
        """Get a specific candidate profile by ID."""
        runtime = self.get_runtime(tenant_id)
        return await runtime.store.get_candidate_profile(user_id, profile_id)

    async def list_candidate_profiles(
        self,
        tenant_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        runtime = self.get_runtime(tenant_id)
        records = await runtime.store.list_candidate_profiles(user_id)
        active_profile_ids = set(await runtime.store.get_active_candidate_profile_ids(user_id))
        payloads: list[dict[str, Any]] = []
        for record in records:
            payloads.append(
                {
                    "user_id": record.user_id,
                    "profile_id": record.profile_id,
                    "active": record.profile_id in active_profile_ids,
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

    async def unset_active_candidate_profile(
        self,
        tenant_id: str,
        user_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        runtime = self.get_runtime(tenant_id)
        await runtime.store.unset_active_candidate_profile(user_id, profile_id)
        profiles = await self.list_candidate_profiles(tenant_id, user_id)
        for profile in profiles:
            if profile["profile_id"] == profile_id:
                return profile
        msg = f"Failed to deactivate candidate profile: {profile_id}"
        raise RuntimeError(msg)

    async def _resolve_candidate_profiles(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> list[ManagedCandidateProfile]:
        runtime = self.get_runtime(tenant_id)
        if user_id is None:
            return []
        resolved_profile_ids: tuple[str, ...]
        if profile_id is not None:
            resolved_profile_ids = (profile_id,)
        else:
            primary_profile_id = await runtime.store.get_active_candidate_profile_id(user_id)
            resolved_profile_ids = (primary_profile_id,) if primary_profile_id is not None else ()
        records: list[ManagedCandidateProfile] = []
        for resolved_profile_id in resolved_profile_ids:
            record = await runtime.store.get_candidate_profile(user_id, resolved_profile_id)
            if record is not None:
                records.append(record)
        return records

    async def _rerank_groups_for_profile(
        self,
        groups: list[JobGroup],
        *,
        tenant_id: str,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> list[JobGroup]:
        records = await self._resolve_candidate_profiles(
            tenant_id,
            user_id=user_id,
            profile_id=profile_id,
        )
        if not records:
            return groups

        from job_ftch.domain import ProfileCatalog
        from job_ftch.nodes.match_scoring import MultiProfileMatchNode

        node = MultiProfileMatchNode(
            ProfileCatalog(
                catalog_name=f"user:{user_id or 'anonymous'}",
                profiles=tuple(
                    search_profile
                    for record in records
                    for search_profile in record.profile.search_profiles
                ),
            )
        )
        scored: list[tuple[float, JobGroup]] = []
        for group in groups:
            job = await node.process(group.canonical_job)
            if job is None:
                continue
            scored_group = group.model_copy(update={"canonical_job": job})
            scored.append((float(job.best_score or 0.0), scored_group))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [group for _, group in scored]

    async def has_candidate_profile_data(
        self,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        """Return True if the user has any profile with at least one example.

        Previously this strictly required the *active* marker to be
        set on at least one profile, which made the first ``/run``
        fail with "Профиль не настроен" if the bot had just saved a
        profile but the activation write had not yet been observed
        by ``get_active_candidate_profile_ids``. The check now also
        accepts a profile with at least one example of *any* kind
        (resume, vacancy, positive, negative) regardless of activation
        status. Profiles that genuinely have no examples still return
        ``False``.
        """
        runtime = self.get_runtime(tenant_id)
        all_records = await runtime.store.list_candidate_profiles(user_id)
        for record in all_records:
            if not record.profile.search_profiles:
                continue
            sp = record.profile.search_profiles[0]
            if (
                sp.positive_example_texts
                or sp.negative_example_texts
                or sp.positive_job_example_texts
                or sp.negative_job_example_texts
            ):
                return True
        return False

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
        since: datetime | None = None,
        user_id: str | None = None,
        profile_id: str | None = None,
        min_score: float | None = None,
    ) -> list[JobRecord]:
        # Rerank a large pool BEFORE truncating, so the top `limit` are the best
        # matches across the whole catalog (not just the most recently updated).
        runtime = self.get_runtime(tenant_id)
        total = await runtime.job_group_store.count(since=since)
        profile_aware = user_id is not None or profile_id is not None
        base_pool = _PROFILE_AWARE_LATEST_JOBS_POOL if profile_aware else _DEFAULT_LATEST_JOBS_POOL
        multiplier = 25 if profile_aware else 10
        pool = min(total or limit, max(limit * multiplier, base_pool))
        groups = await runtime.job_group_store.list_groups(limit=pool, since=since)
        ranked = await self._rerank_groups_for_profile(
            groups,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
        )
        jobs = [group.canonical_job for group in ranked]
        if min_score is not None:
            jobs = [job for job in jobs if (job.best_score or 0.0) >= min_score]
        return jobs[:limit]

    def default_tenant_id(self) -> str:
        ids = self.tenant_ids()
        if not ids:
            msg = "No tenants configured"
            raise RuntimeError(msg)
        return ids[0]

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
        async with _tenant_run_lock(runtime.settings, tenant_id):
            await runtime.store.reset_namespace()
            runtime.runtime_sources.clear()
            runtime.disabled_source_ids.clear()
            runtime.sources_loaded = False
            self._apply_runtime_sources(runtime)

    async def clear_dedup(self, tenant_id: str) -> int:
        """Clear dedup and processed-item state for a tenant. Returns number of deleted dedup records."""
        runtime = self.get_runtime(tenant_id)
        async with _tenant_run_lock(runtime.settings, tenant_id):
            return await runtime.store.clear_dedup_state()

    async def _clear_all_unlocked(self, tenant_id: str) -> tuple[int, int, int, int]:
        runtime = self.get_runtime(tenant_id)
        dedup = await runtime.store.clear_dedup_state()
        jobs = 0
        count_jobs = getattr(runtime.job_backend, "count_jobs", None)
        if callable(count_jobs):
            jobs = int(await count_jobs())
        groups = 0
        clear_groups = getattr(runtime.job_group_store, "clear", None)
        if callable(clear_groups):
            groups = await clear_groups()
        vectors = 0
        vb = getattr(runtime, "vector_backend", None)
        clear_vectors = getattr(vb, "clear", None)
        if callable(clear_vectors):
            try:
                vectors = await clear_vectors()
            except Exception as exc:  # vector store is best-effort, don't fail the whole clear
                structlog.get_logger(__name__).warning("vector_clear_failed", error=str(exc))
        return dedup, jobs, groups, vectors

    async def clear_all(self, tenant_id: str) -> tuple[int, int, int, int]:
        """Clear dedup state, jobs, job groups, and vector store."""
        runtime = self.get_runtime(tenant_id)
        async with _tenant_run_lock(runtime.settings, tenant_id):
            return await self._clear_all_unlocked(tenant_id)

    async def clear_run_data(self, tenant_id: str) -> dict[str, int]:
        """Clear all data that can make a scripted run inherit a previous run."""
        if len(self.tenant_ids()) != 1:
            raise RuntimeError(
                "Scripted clean is single-tenant because job catalog and vector cleanup "
                "are still global."
            )
        runtime = self.get_runtime(tenant_id)
        async with _tenant_run_lock(runtime.settings, tenant_id):
            store_counts = await runtime.store.clear_run_artifacts()
            dedup, jobs, groups, vectors = await self._clear_all_unlocked(tenant_id)
            return {
                **store_counts,
                "dedup_records": dedup,
                "jobs": jobs,
                "job_groups": groups,
                "vectors": vectors,
            }

    async def close(self) -> None:
        sessions = self._operator_sessions
        if sessions is not None:
            closer = getattr(sessions, "close_all", None)
            if callable(closer):
                await closer()
            self._operator_sessions = None
        closed: set[int] = set()
        try:
            for runtime in self._runtimes.values():
                for obj in (
                    runtime.store,
                    runtime.search_backend,
                    runtime.job_backend,
                    runtime.job_group_store,
                    runtime.vector_backend,
                ):
                    if obj is None:
                        continue
                    ident = id(obj)
                    if ident in closed:
                        continue
                    close = getattr(obj, "close", None)
                    if callable(close):
                        await close()
                    closed.add(ident)
        finally:
            # Full teardown: every run for this process is done, so force-kill
            # any browser/driver descendant the bypass stack orphaned. Only
            # current-process descendants are targeted, never the user's own
            # Chrome. Without this a lingering child keeps the interpreter alive
            # on Windows, where the per-open_page reaper only clears stale leaves.
            from job_ftch.infrastructure.sources.browser_utils import (
                terminate_browser_descendants,
            )

            terminate_browser_descendants()
