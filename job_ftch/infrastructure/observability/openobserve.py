"""Direct OTLP/HTTP operational telemetry export to OpenObserve."""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from opentelemetry import metrics
from opentelemetry.metrics import Observation

if TYPE_CHECKING:
    from job_ftch.application.pipeline import RunSummary
    from job_ftch.config import Settings
    from job_ftch.domain import SourceHealth


logger = logging.getLogger(__name__)
_configured = False
_shutdown = False
_meter_provider: Any | None = None
_logger_provider: Any | None = None
_item_counters: dict[str, Any] = {}
_duration_histogram: Any | None = None
_cost_histogram: Any | None = None
_runtime_gauges_registered = False
_runtime_state_snapshots: dict[str, list[tuple[float, dict[str, str]]]] = {}

_CORRELATION_FIELDS = frozenset(
    {
        "source_run_id",
        "tenant_id",
        "graph_hash",
        "source_id",
        "source_kind",
        "source_name",
        "node_id",
    }
)


def _counter_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _counter_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): count for key, count in value.items()}


class _ContextAttributesFilter(logging.Filter):
    """Expose structlog correlation context as searchable OTLP attributes.

    The rendered JSON event remains the log body, while these fields become
    first-class OpenObserve columns.  This keeps queries cheap and avoids
    parsing every JSON body merely to join logs with Langfuse traces.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from structlog.contextvars import get_contextvars

        for key, value in get_contextvars().items():
            if key in _CORRELATION_FIELDS and isinstance(value, (str, int, float, bool)):
                setattr(record, key, value)
        record.level = record.levelname.lower()
        message = record.getMessage()
        if isinstance(message, str) and message.startswith("{"):
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                event = payload.get("event")
                if isinstance(event, str) and event:
                    record.event = event
        return True


def configure_openobserve(settings: Settings) -> bool:
    """Enable only logs and metrics; Langfuse remains the trace destination."""
    global _configured, _logger_provider, _meter_provider
    if _configured:
        return True
    if not settings.openobserve_enabled:
        return False
    if not (
        settings.openobserve_url and settings.openobserve_username and settings.openobserve_password
    ):
        logger.warning("OpenObserve enabled but URL or credentials are missing.")
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    except ImportError:
        logger.error(
            "OpenObserve enabled but the OTLP HTTP exporter is not installed. "
            "Install job-ftch[tracing]."
        )
        return False

    base = _resolve_openobserve_url(settings.openobserve_url)
    org = quote(settings.openobserve_org.strip() or "default", safe="")
    secret = settings.openobserve_password.get_secret_value()
    auth = base64.b64encode(f"{settings.openobserve_username}:{secret}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    resource = Resource.create(
        {
            SERVICE_NAME: settings.telemetry_service_name,
            "deployment.environment": os.environ.get("JOB_FTCH_ENV", "dev"),
        }
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=f"{base}/api/{org}/v1/logs",
                headers={**headers, "stream-name": settings.openobserve_logs_stream},
                timeout=settings.openobserve_timeout_seconds,
            )
        )
    )
    log_handler = LoggingHandler(logger_provider=logger_provider)
    log_handler.setLevel(logging.INFO)
    log_handler.addFilter(_ContextAttributesFilter())
    # The Telegram adapter is a sibling package, not a child of `job_ftch`,
    # so attach the same handler to both application namespaces.
    for namespace in ("job_ftch", "job_ftch.adapters.telegram_bot"):
        namespace_logger = logging.getLogger(namespace)
        namespace_logger.setLevel(logging.INFO)
        namespace_logger.addHandler(log_handler)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=f"{base}/api/{org}/v1/metrics",
            headers=headers,
            timeout=settings.openobserve_timeout_seconds,
        ),
        export_interval_millis=settings.openobserve_metric_export_interval_ms,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _logger_provider = logger_provider
    _meter_provider = meter_provider
    _configured = True
    _register_runtime_state_gauges(metrics.get_meter("job_ftch.runtime"))
    atexit.register(shutdown_openobserve)
    logger.info("OpenObserve operational telemetry configured at %s", base)
    return True


def _resolve_openobserve_url(url: str) -> str:
    """Use the Docker gateway only in Docker; host scripts use localhost."""
    base = url.strip().rstrip("/")
    running_in_container = Path("/.dockerenv").exists() or os.environ.get(
        "CONTAINER", ""
    ).lower() in {"docker", "podman"}
    if not running_in_container:
        base = base.replace("host.docker.internal", "localhost")
    return base


def record_run_metrics(summary: RunSummary) -> None:
    """Export compact per-run and per-source acquisition metrics."""
    if not _configured:
        return
    meter = metrics.get_meter("job_ftch.ingest")
    attrs = _run_attrs(summary)
    _counter(meter, "job_ftch.ingest.runs").add(1, attrs)
    for field in (
        "fetched",
        "sanitized",
        "triaged",
        "extracted",
        "partial",
        "review",
        "dropped",
        "duplicates",
        "emitted",
        "posted",
        "rejected",
        "failed",
        "quarantined",
        "deferred",
        "new_groups_created",
        "merged_into_group",
        "monitored",
        "scraped",
    ):
        _counter(meter, f"job_ftch.ingest.items.{field}").add(getattr(summary, field), attrs)
    _duration(meter).record(_duration_seconds(summary), attrs)
    if summary.llm_cost_is_complete:
        _cost(meter).record(summary.llm_cost_usd, attrs)
    for field in (
        "llm_usage_requests",
        "llm_tokens_in",
        "llm_cached_tokens_in",
        "llm_tokens_out",
    ):
        _counter(meter, f"job_ftch.ingest.{field}").add(getattr(summary, field), attrs)
    _counter(meter, "job_ftch.ingest.cost_complete").add(
        1 if summary.llm_cost_is_complete else 0,
        {**attrs, "pricing_version": summary.llm_cost_pricing_version or "unknown"},
    )

    for node_id, node_stats in summary.graph_node_metrics.items():
        node_attrs = {**attrs, "graph_hash": summary.graph_hash or "unknown", "node_id": node_id}
        _counter(meter, "job_ftch.graph.node.calls").add(
            _counter_value(node_stats.get("calls", 0)), node_attrs
        )
        for outcome, count in _counter_mapping(node_stats.get("outcomes", {})).items():
            _counter(meter, "job_ftch.graph.node.outcomes").add(
                _counter_value(count), {**node_attrs, "outcome": outcome}
            )
        for status, count in _counter_mapping(node_stats.get("terminal_statuses", {})).items():
            _counter(meter, "job_ftch.graph.terminal_statuses").add(
                _counter_value(count), {**node_attrs, "status": status}
            )
        for reason, count in _counter_mapping(node_stats.get("terminal_reasons", {})).items():
            _counter(meter, "job_ftch.graph.terminal_reasons").add(
                _counter_value(count), {**node_attrs, "reason": reason}
            )

    for source_id, stats in summary.by_source_id.items():
        source_kind, _, source_name = source_id.partition(":")
        source_attrs = {
            **attrs,
            "source_kind": source_kind or "unknown",
            "source_id": source_id,
            "source_name": source_name or source_id,
        }
        for field in (
            "fetched",
            "sanitized",
            "triaged",
            "extracted",
            "partial",
            "review",
            "dropped",
            "duplicates",
            "emitted",
            "posted",
            "rejected",
            "failed",
            "quarantined",
            "deferred",
        ):
            _counter(meter, f"job_ftch.ingest.items.{field}").add(
                getattr(stats, field), source_attrs
            )
    for source_outcome in summary.source_outcomes:
        status = str(source_outcome.get("status") or "unknown")
        source_id = str(source_outcome.get("source_id") or "unknown")
        source_kind, _, source_name = source_id.partition(":")
        _counter(meter, "job_ftch.ingest.source.outcomes").add(
            1,
            {
                **attrs,
                "source_kind": source_kind or "unknown",
                "source_id": source_id,
                "source_name": source_name or source_id,
                "status": status,
            },
        )
        for stage in (
            "yielded",
            "monitored",
            "scraped",
            "freshness_filtered",
            "parser_duplicates_suppressed",
        ):
            _counter(meter, "job_ftch.ingest.source.items").add(
                _counter_value(source_outcome.get(stage, 0)),
                {
                    **attrs,
                    "source_kind": source_kind or "unknown",
                    "source_id": source_id,
                    "source_name": source_name or source_id,
                    "stage": stage,
                },
            )
    force_flush_openobserve()


def record_bot_delivery_metrics(
    summary: RunSummary,
    *,
    persisted_candidates: int,
    eligible_to_send: int,
    chat_sent: int,
    channel_posted: int,
    chat_target_unusable: bool = False,
    channel_target_unusable: bool = False,
    chat_transient_failure: bool = False,
    channel_transient_failure: bool = False,
) -> None:
    """Record the post-pipeline delivery conversion with unambiguous stages."""
    if not _configured:
        return
    meter = metrics.get_meter("job_ftch.bot")
    attrs = _run_attrs(summary)
    for stage, count in (
        ("routing_accepted", summary.emitted),
        ("persisted_candidates", persisted_candidates),
        ("eligible_to_send", eligible_to_send),
        ("chat_sent", chat_sent),
        ("channel_posted", channel_posted),
        ("chat_target_unusable", int(chat_target_unusable)),
        ("channel_target_unusable", int(channel_target_unusable)),
        ("chat_transient_failure", int(chat_transient_failure)),
        ("channel_transient_failure", int(channel_transient_failure)),
    ):
        _counter(meter, "job_ftch.bot.delivery.items").add(int(count), {**attrs, "stage": stage})
    force_flush_openobserve()


def record_runtime_state_metrics(
    summary: RunSummary,
    *,
    source_health: list[SourceHealth],
    scheduler_state: dict[str, str | None],
) -> None:
    """Publish current store-backed runtime state as observable gauges."""
    if not _configured:
        return
    attrs = _run_attrs(summary)
    source_rows: dict[str, list[tuple[float, dict[str, str]]]] = {
        "job_ftch.source.health.status": [],
        "job_ftch.source.health.paused": [],
        "job_ftch.source.health.degraded": [],
        "job_ftch.source.health.failure_streak": [],
        "job_ftch.source.health.last_success_age": [],
        "job_ftch.source.health.last_emitted": [],
        "job_ftch.source.quality.reliable": [],
        "job_ftch.source.quality.rich": [],
        "job_ftch.source.quality.high_relevance": [],
        "job_ftch.source.quality.ok_rate": [],
        "job_ftch.source.quality.yield_rate": [],
        "job_ftch.source.quality.relevant_rate": [],
        "job_ftch.source.quality.window_runs": [],
    }
    for health in source_health:
        source_attrs = {
            **attrs,
            "source_id": health.source_id,
            "source_kind": health.source_kind,
            "source_name": health.source_name,
        }
        for status in ("healthy", "failing", "degraded", "paused", "disabled", "pending"):
            source_rows["job_ftch.source.health.status"].append(
                (1.0 if health.status == status else 0.0, {**source_attrs, "status": status})
            )
        source_rows["job_ftch.source.health.paused"].append(
            (1.0 if health.paused else 0.0, source_attrs)
        )
        source_rows["job_ftch.source.health.degraded"].append(
            (1.0 if health.degraded else 0.0, source_attrs)
        )
        source_rows["job_ftch.source.health.failure_streak"].append(
            (float(health.failure_streak), source_attrs)
        )
        source_rows["job_ftch.source.health.last_success_age"].append(
            (_age_seconds(health.last_success_at), source_attrs)
        )
        source_rows["job_ftch.source.health.last_emitted"].append(
            (float(health.last_emitted), source_attrs)
        )
        source_rows["job_ftch.source.quality.reliable"].append(
            (1.0 if health.quality_reliable else 0.0, source_attrs)
        )
        source_rows["job_ftch.source.quality.rich"].append(
            (1.0 if health.quality_rich else 0.0, source_attrs)
        )
        source_rows["job_ftch.source.quality.high_relevance"].append(
            (1.0 if health.quality_high_relevance else 0.0, source_attrs)
        )
        source_rows["job_ftch.source.quality.ok_rate"].append(
            (float(health.quality_ok_rate), source_attrs)
        )
        source_rows["job_ftch.source.quality.yield_rate"].append(
            (float(health.quality_yield_rate), source_attrs)
        )
        source_rows["job_ftch.source.quality.relevant_rate"].append(
            (float(health.quality_relevant_rate), source_attrs)
        )
        source_rows["job_ftch.source.quality.window_runs"].append(
            (float(health.quality_window_runs), source_attrs)
        )

    scheduler_attrs = attrs
    scheduler_rows = {
        "job_ftch.runtime.last_run_finished_age": [
            (_datetime_age_seconds(summary.finished_at), scheduler_attrs)
        ],
        "job_ftch.runtime.last_run_failed": [(1.0 if summary.failed > 0 else 0.0, scheduler_attrs)],
        "job_ftch.bot.scheduler.last_attempt_age": [
            (_age_seconds(scheduler_state.get("bot_scheduler:last_attempt_at")), scheduler_attrs)
        ],
        "job_ftch.bot.scheduler.last_success_age": [
            (_age_seconds(scheduler_state.get("bot_scheduler:last_success_at")), scheduler_attrs)
        ],
        "job_ftch.bot.scheduler.last_publish_success_age": [
            (
                _age_seconds(scheduler_state.get("bot_scheduler:last_publish_success_at")),
                scheduler_attrs,
            )
        ],
        "job_ftch.bot.scheduler.pending_publish_age": [
            (
                _age_seconds(scheduler_state.get("bot_scheduler:pending_publish_since")),
                scheduler_attrs,
            )
        ],
        "job_ftch.bot.scheduler.publish_error_present": [
            (
                1.0
                if str(scheduler_state.get("bot_scheduler:last_publish_error") or "").strip()
                else 0.0,
                scheduler_attrs,
            )
        ],
        "job_ftch.bot.scheduler.last_publish_sent": [
            (
                float(_counter_value(scheduler_state.get("bot_scheduler:last_publish_sent") or 0)),
                scheduler_attrs,
            )
        ],
    }
    _runtime_state_snapshots.update(source_rows)
    _runtime_state_snapshots.update(scheduler_rows)
    force_flush_openobserve()


def force_flush_openobserve() -> None:
    for provider in (_logger_provider, _meter_provider):
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush(timeout_millis=5_000)


def shutdown_openobserve() -> None:
    global _shutdown
    if _shutdown:
        return
    _shutdown = True
    for provider in (_logger_provider, _meter_provider):
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _counter(meter: Any, name: str) -> Any:
    instrument = _item_counters.get(name)
    if instrument is None:
        instrument = meter.create_counter(name, unit="items")
        _item_counters[name] = instrument
    return instrument


def _duration(meter: Any) -> Any:
    global _duration_histogram
    if _duration_histogram is None:
        _duration_histogram = meter.create_histogram("job_ftch.ingest.run.duration", unit="s")
    return _duration_histogram


def _cost(meter: Any) -> Any:
    global _cost_histogram
    if _cost_histogram is None:
        _cost_histogram = meter.create_histogram("job_ftch.ingest.llm.cost", unit="USD")
    return _cost_histogram


def _register_runtime_state_gauges(meter: Any) -> None:
    global _runtime_gauges_registered
    if _runtime_gauges_registered:
        return
    for name, unit in (
        ("job_ftch.source.health.status", "1"),
        ("job_ftch.source.health.paused", "1"),
        ("job_ftch.source.health.degraded", "1"),
        ("job_ftch.source.health.failure_streak", "failures"),
        ("job_ftch.source.health.last_success_age", "s"),
        ("job_ftch.source.health.last_emitted", "items"),
        ("job_ftch.runtime.last_run_finished_age", "s"),
        ("job_ftch.runtime.last_run_failed", "1"),
        ("job_ftch.bot.scheduler.last_attempt_age", "s"),
        ("job_ftch.bot.scheduler.last_success_age", "s"),
        ("job_ftch.bot.scheduler.last_publish_success_age", "s"),
        ("job_ftch.bot.scheduler.pending_publish_age", "s"),
        ("job_ftch.bot.scheduler.publish_error_present", "1"),
        ("job_ftch.bot.scheduler.last_publish_sent", "items"),
    ):
        meter.create_observable_gauge(name, callbacks=[_gauge_callback(name)], unit=unit)
    _runtime_gauges_registered = True


def _gauge_callback(name: str) -> Any:
    def callback(_: Any) -> list[Observation]:
        return [
            Observation(value, attrs) for value, attrs in _runtime_state_snapshots.get(name, [])
        ]

    return callback


def _age_seconds(value: str | None) -> float:
    if not value:
        return -1.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return -1.0
    return _datetime_age_seconds(parsed)


def _datetime_age_seconds(value: datetime | None) -> float:
    if value is None:
        return -1.0
    parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return max(time.time() - parsed.timestamp(), 0.0)


def _duration_seconds(summary: RunSummary) -> float:
    if summary.finished_at is None:
        return 0.0
    started = summary.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    finished = summary.finished_at
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return max((finished - started).total_seconds(), 0.0)


def _run_attrs(summary: RunSummary) -> dict[str, str]:
    """Stable attributes shared with the terminal Langfuse trace."""
    return {
        "tenant_id": summary.tenant_id or "unknown",
        "source_run_id": summary.source_run_id or "unknown",
        "graph_hash": summary.graph_hash or "unknown",
    }
