"""Prometheus metrics exporter for tenant pipeline summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from job_ftch.application.pipeline import RunSummary


class PrometheusExporter:
    _started_ports: ClassVar[set[int]] = set()

    def __init__(
        self, *, registry: Any | None = None, start_server: bool = False, port: int = 9090
    ) -> None:
        try:
            from prometheus_client import Counter, Gauge, Histogram, start_http_server
        except ImportError as exc:
            msg = "prometheus-client is required for metrics export. Install with: pip install 'job-ftch[metrics]'"
            raise ImportError(msg) from exc

        label_names = ("tenant_id", "source_kind")
        self._start_http_server = start_http_server
        self._port = port
        self._items_fetched = Counter(
            "job_ftch_items_fetched_total",
            "Total fetched items by tenant and source kind.",
            label_names,
            registry=registry,
        )
        self._items_extracted = Counter(
            "job_ftch_items_extracted_total",
            "Total extracted job-like items by tenant and source kind.",
            label_names,
            registry=registry,
        )
        self._items_dropped = Counter(
            "job_ftch_items_dropped_total",
            "Total dropped items by tenant, source kind, and reason.",
            ("tenant_id", "source_kind", "reason"),
            registry=registry,
        )
        self._items_failed = Counter(
            "job_ftch_items_failed_total",
            "Total failed items by tenant and source kind.",
            label_names,
            registry=registry,
        )
        self._run_duration = Histogram(
            "job_ftch_run_duration_seconds",
            "Pipeline run duration in seconds by tenant.",
            ("tenant_id",),
            registry=registry,
        )
        self._job_groups_total = Gauge(
            "job_ftch_job_groups_total",
            "Current total number of job groups by tenant.",
            ("tenant_id",),
            registry=registry,
        )
        if start_server:
            self.start_server(port=port)

    def start_server(self, *, port: int | None = None) -> None:
        listen_port = port or self._port
        if listen_port in self._started_ports:
            return
        self._start_http_server(listen_port)
        self._started_ports.add(listen_port)

    async def observe_run(
        self,
        summary: RunSummary,
        *,
        tenant_id: str,
        job_group_total: int | None = None,
    ) -> None:
        for source_kind, stats in summary.by_source_kind.items():
            labels = {"tenant_id": tenant_id, "source_kind": source_kind}
            if stats.fetched:
                self._items_fetched.labels(**labels).inc(stats.fetched)
            if stats.extracted:
                self._items_extracted.labels(**labels).inc(stats.extracted)
            if stats.failed:
                self._items_failed.labels(**labels).inc(stats.failed)
            for reason, count in stats.drop_reasons.items():
                if count:
                    self._items_dropped.labels(
                        tenant_id=tenant_id,
                        source_kind=source_kind,
                        reason=reason,
                    ).inc(count)

        if summary.started_at is not None and summary.finished_at is not None:
            duration = max((summary.finished_at - summary.started_at).total_seconds(), 0.0)
            self._run_duration.labels(tenant_id=tenant_id).observe(duration)
        if job_group_total is not None:
            self._job_groups_total.labels(tenant_id=tenant_id).set(job_group_total)
