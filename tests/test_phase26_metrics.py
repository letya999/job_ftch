from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from job_ftch.application.pipeline import RunSummary
from job_ftch.domain import RawItem, SourceKind, TenantConfig


class _MetricChild:
    def __init__(self, metric: "_MetricBase", labels: dict[str, str]) -> None:
        self.metric = metric
        self.labels = labels

    def inc(self, amount: float = 1.0) -> None:
        self.metric.calls.append(("inc", self.labels, amount))

    def observe(self, value: float) -> None:
        self.metric.calls.append(("observe", self.labels, value))

    def set(self, value: float) -> None:
        self.metric.calls.append(("set", self.labels, value))


class _MetricBase:
    def __init__(self, name: str, *_args: Any, **_kwargs: Any) -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def labels(self, **labels: str) -> _MetricChild:
        return _MetricChild(self, labels)


def _fake_prometheus_module(start_calls: list[int]) -> Any:
    def _start_http_server(port: int) -> None:
        start_calls.append(port)

    return SimpleNamespace(
        Counter=_MetricBase,
        Gauge=_MetricBase,
        Histogram=_MetricBase,
        start_http_server=_start_http_server,
    )


def _write_fixture(path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text="Senior ML Engineer\nRemote\nCompany: OpenAI\nSalary: USD 100000 - 150000",
        metadata={"company": "OpenAI", "title": "Senior ML Engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


def test_prometheus_exporter_records_summary_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    start_calls: list[int] = []
    monkeypatch.setitem(sys.modules, "prometheus_client", _fake_prometheus_module(start_calls))

    from job_ftch.infrastructure.metrics.prometheus import PrometheusExporter

    summary = RunSummary(
        tenant_id="ai_jobs",
        started_at=datetime(2026, 6, 12, tzinfo=UTC),
        finished_at=datetime(2026, 6, 12, tzinfo=UTC) + timedelta(seconds=12),
    )
    stats = summary.source_stats("career_site")
    stats.fetched = 3
    stats.extracted = 2
    stats.failed = 1
    stats.drop_reasons["already_processed"] = 4

    exporter = PrometheusExporter(start_server=True, port=9909)

    import asyncio

    asyncio.run(exporter.observe_run(summary, tenant_id="ai_jobs", job_group_total=7))

    assert start_calls == [9909]
    assert exporter._items_fetched.calls == [("inc", {"tenant_id": "ai_jobs", "source_kind": "career_site"}, 3)]
    assert exporter._items_extracted.calls == [("inc", {"tenant_id": "ai_jobs", "source_kind": "career_site"}, 2)]
    assert exporter._items_failed.calls == [("inc", {"tenant_id": "ai_jobs", "source_kind": "career_site"}, 1)]
    assert exporter._items_dropped.calls == [
        ("inc", {"tenant_id": "ai_jobs", "source_kind": "career_site", "reason": "already_processed"}, 4)
    ]
    assert exporter._job_groups_total.calls == [("set", {"tenant_id": "ai_jobs"}, 7)]
    assert exporter._run_duration.calls == [("observe", {"tenant_id": "ai_jobs"}, 12.0)]


@pytest.mark.asyncio
async def test_tenant_runner_emits_prometheus_metrics(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)

    observed: list[tuple[str, int, int]] = []

    class _ExporterStub:
        def __init__(self, *, start_server: bool = False, port: int = 9090) -> None:
            self.start_server = start_server
            self.port = port

        async def observe_run(
            self,
            summary: RunSummary,
            *,
            tenant_id: str,
            job_group_total: int | None = None,
        ) -> None:
            observed.append((tenant_id, summary.emitted, int(job_group_total or 0)))

    monkeypatch.setattr("job_ftch.application.tenant_runner.PrometheusExporter", _ExporterStub)

    from job_ftch.application.tenant_runner import TenantRunner

    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "metrics_enabled": True,
            "metrics_port": 9910,
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )

    runner = TenantRunner.from_tenants([tenant])
    try:
        summary = await runner.run_tenant("ai_jobs")
        assert summary.emitted == 1
        assert observed == [("ai_jobs", 1, 1)]
    finally:
        await runner.close()
