from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from structlog.contextvars import bind_contextvars, reset_contextvars

from job_ftch.application.pipeline import RunSummary
from job_ftch.domain import SourceHealth
from job_ftch.infrastructure.observability.openobserve import (
    _ContextAttributesFilter,
    _counter_mapping,
    _counter_value,
    _resolve_openobserve_url,
    _runtime_state_snapshots,
    record_runtime_state_metrics,
)


def test_safe_trace_span_discards_sensitive_fields_and_exceptions() -> None:
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace.status import Status, StatusCode

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    span = ReadableSpan(
        name="pipeline.run",
        attributes={
            "job_ftch.source_run_id": "a" * 32,
            "job_ftch.fetched": 2,
            "job_ftch.llm.observation.input": "PRIVATE VACANCY",
            "credentials": "PRIVATE TOKEN",
        },
        events=(Event("exception", {"exception.message": "PRIVATE ERROR"}),),
        status=Status(StatusCode.ERROR, "PRIVATE ERROR"),
    )
    safe = _safe_trace_span(span)
    assert safe.name == "pipeline.run"
    assert safe.attributes == {"job_ftch.source_run_id": "a" * 32, "job_ftch.fetched": 2}
    assert not safe.events
    assert safe.status.status_code is StatusCode.ERROR
    assert safe.status.description is None


def test_safe_trace_span_rejects_untrusted_names_and_attribute_values() -> None:
    from opentelemetry.sdk.trace import ReadableSpan

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    span = ReadableSpan(
        name="PRIVATE VACANCY",
        attributes={"job_ftch.source_run_id": "PRIVATE TOKEN", "job_ftch.fetched": "PRIVATE TEXT"},
    )
    safe = _safe_trace_span(span)
    assert safe.name == "job_ftch.span"
    assert not safe.attributes


def test_safe_trace_span_does_not_forward_resource_secrets() -> None:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    span = ReadableSpan(name="pipeline.run", resource=Resource({"credential": "PRIVATE"}))
    safe = _safe_trace_span(span)
    assert safe.resource.attributes == {"service.name": "job_ftch"}


def test_safe_trace_span_preserves_known_operations_and_numeric_counters() -> None:
    from opentelemetry.sdk.trace import ReadableSpan

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    span = ReadableSpan(
        name="pipeline.item",
        attributes={"job_ftch.extracted": 4, "job_ftch.emitted": 3},
    )
    safe = _safe_trace_span(span)
    assert safe.name == "pipeline.item"
    assert safe.attributes == {"job_ftch.extracted": 4, "job_ftch.emitted": 3}


def test_safe_trace_span_keeps_supported_pipeline_operation_names() -> None:
    from opentelemetry.sdk.trace import ReadableSpan

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    names = (
        "ingest.run",
        "bot.run",
        "pipeline.node",
        "pipeline.item.decision",
        "extraction.generation",
        "post_type.classify",
        "match_scoring.score",
        "hard_filter.check",
        "garbage_filter.check",
        "semantic_prefilter.check",
        "tfidf_logreg_prefilter.check",
    )
    assert [_safe_trace_span(ReadableSpan(name=name)).name for name in names] == list(names)
    assert _safe_trace_span(ReadableSpan(name="graph.node.PRIVATE")).name == "graph.node"


def test_safe_trace_span_keeps_ids_but_drops_untrusted_trace_state() -> None:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import SpanContext, TraceFlags, TraceState

    from job_ftch.infrastructure.observability.openobserve import _safe_trace_span

    context = SpanContext(
        trace_id=42,
        span_id=23,
        is_remote=False,
        trace_flags=TraceFlags(1),
        trace_state=TraceState([("vendor", "PRIVATE")]),
    )
    parent = SpanContext(
        trace_id=42,
        span_id=12,
        is_remote=True,
        trace_flags=TraceFlags(1),
        trace_state=TraceState([("vendor", "PRIVATE")]),
    )
    safe = _safe_trace_span(
        ReadableSpan(
            name="pipeline.run", context=context, parent=parent, start_time=123, end_time=456
        )
    )
    assert safe.context.trace_id == 42 and safe.context.span_id == 23
    assert safe.parent.span_id == 12
    assert len(safe.context.trace_state) == len(safe.parent.trace_state) == 0
    assert (safe.start_time, safe.end_time) == (123, 456)


def test_trace_processor_exports_only_sanitized_spans() -> None:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from job_ftch.infrastructure.observability.openobserve import _SafeTraceProcessor

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"credential": "PRIVATE"}))
    provider.add_span_processor(_SafeTraceProcessor(exporter))
    try:
        with provider.get_tracer("test").start_as_current_span("pipeline.run") as span:
            span.set_attribute("job_ftch.fetched", 4)
            span.set_attribute("password", "PRIVATE")
            span.add_event("exception", {"exception.message": "PRIVATE"})
        assert provider.force_flush()
        sent = exporter.get_finished_spans()
        assert len(sent) == 1
        assert sent[0].attributes == {"job_ftch.fetched": 4}
        assert not sent[0].events
        assert sent[0].resource.attributes == {"service.name": "job_ftch"}
    finally:
        provider.shutdown()


def test_trace_processor_uses_bounded_batch_queue(monkeypatch) -> None:
    from unittest.mock import Mock

    from job_ftch.infrastructure.observability.openobserve import _SafeTraceProcessor

    factory = Mock()
    monkeypatch.setattr("opentelemetry.sdk.trace.export.BatchSpanProcessor", factory)
    _SafeTraceProcessor(object())
    assert factory.call_args.kwargs["max_queue_size"] == 2048
    assert factory.call_args.kwargs["max_export_batch_size"] == 128


def test_trace_processor_never_forwards_raw_span_on_start(monkeypatch) -> None:
    from unittest.mock import Mock

    from job_ftch.infrastructure.observability.openobserve import _SafeTraceProcessor

    delegate = Mock()
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.export.BatchSpanProcessor", lambda *a, **k: delegate
    )
    processor = _SafeTraceProcessor(object())
    processor.on_start("PRIVATE")
    delegate.on_start.assert_not_called()


def test_trace_sanitizer_failure_does_not_break_application(monkeypatch, caplog) -> None:
    from unittest.mock import Mock

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from job_ftch.infrastructure.observability import openobserve

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(openobserve._SafeTraceProcessor(exporter))
    monkeypatch.setattr(openobserve, "_safe_trace_span", Mock(side_effect=RuntimeError("PRIVATE")))
    try:
        with provider.get_tracer("test").start_as_current_span("pipeline.run"):
            pass
        assert provider.force_flush()
        assert not exporter.get_finished_spans()
        assert "PRIVATE" not in caplog.text
    finally:
        provider.shutdown()


def test_configured_trace_export_sends_only_sanitized_data(monkeypatch) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic import SecretStr

    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability import openobserve

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    options = {}

    def make_exporter(**kwargs):
        options.update(kwargs)
        return exporter

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", make_exporter
    )
    settings = Settings.model_validate(
        {
            "tracing_enabled": True,
            "openobserve_enabled": True,
            "openobserve_url": "http://127.0.0.1:5080",
            "openobserve_username": "test",
            "openobserve_password": SecretStr("private"),
        }
    )
    try:
        assert openobserve._configure_safe_trace_export(settings)
        assert openobserve._configure_safe_trace_export(settings)
        with provider.get_tracer("test").start_as_current_span("pipeline.run") as span:
            span.set_attribute("job_ftch.fetched", 3)
            span.set_attribute("password", "PRIVATE")
        assert provider.force_flush()
        assert options["endpoint"] == "http://127.0.0.1:5080/api/default/v1/traces"
        assert options["headers"]["stream-name"] == "job_ftch_traces"
        assert len(exporter.get_finished_spans()) == 1
        assert exporter.get_finished_spans()[0].attributes == {"job_ftch.fetched": 3}
    finally:
        provider.shutdown()


def test_trace_export_stays_disabled_without_opt_in_and_credentials(monkeypatch) -> None:
    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability.openobserve import _configure_safe_trace_export

    def unexpected(**kwargs):
        raise AssertionError("exporter must not be created")

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", unexpected
    )
    assert not _configure_safe_trace_export(Settings.model_validate({"openobserve_enabled": True}))
    assert not _configure_safe_trace_export(
        Settings.model_validate(
            {
                "tracing_enabled": True,
                "openobserve_enabled": True,
                "openobserve_url": None,
                "openobserve_username": None,
                "openobserve_password": None,
            }
        )
    )


def test_trace_export_initializes_missing_provider(monkeypatch) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic import SecretStr

    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability.openobserve import _configure_safe_trace_export

    installed = []
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: trace.ProxyTracerProvider())
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **kwargs: InMemorySpanExporter(),
    )
    settings = Settings.model_validate(
        {
            "tracing_enabled": True,
            "openobserve_enabled": True,
            "openobserve_url": "http://127.0.0.1:5080",
            "openobserve_username": "test",
            "openobserve_password": SecretStr("private"),
        }
    )
    assert _configure_safe_trace_export(settings)
    assert len(installed) == 1
    installed[0].shutdown()


def test_trace_export_configuration_is_idempotent_under_concurrency(monkeypatch) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic import SecretStr

    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability.openobserve import _configure_safe_trace_export

    provider = TracerProvider()
    exporters = []

    def make_exporter(**kwargs):
        time.sleep(0.05)
        exporter = InMemorySpanExporter()
        exporters.append(exporter)
        return exporter

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", make_exporter
    )
    settings = Settings.model_validate(
        {
            "tracing_enabled": True,
            "openobserve_enabled": True,
            "openobserve_url": "http://127.0.0.1:5080",
            "openobserve_username": "test",
            "openobserve_password": SecretStr("fake"),
        }
    )
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(pool.map(_configure_safe_trace_export, [settings] * 4))
        assert len(exporters) == 1
    finally:
        provider.shutdown()


def test_trace_export_does_not_replace_external_provider(monkeypatch) -> None:
    from opentelemetry import trace
    from pydantic import SecretStr

    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability.openobserve import _configure_safe_trace_export

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: trace.NoOpTracerProvider())
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected exporter")),
    )
    settings = Settings.model_validate(
        {
            "tracing_enabled": True,
            "openobserve_enabled": True,
            "openobserve_url": "http://127.0.0.1:5080",
            "openobserve_username": "test",
            "openobserve_password": SecretStr("private"),
        }
    )
    assert not _configure_safe_trace_export(settings)


def test_observability_configures_safe_trace_export(monkeypatch) -> None:
    from job_ftch.config import Settings
    from job_ftch.infrastructure import observability
    from job_ftch.infrastructure.observability import openobserve

    calls = []
    monkeypatch.setattr(observability, "configure_tracing", lambda settings: None)
    monkeypatch.setattr(observability, "configure_openobserve", lambda settings: True)
    monkeypatch.setattr(
        openobserve, "_configure_safe_trace_export", lambda settings: calls.append(settings)
    )
    settings = Settings.model_validate({"tracing_enabled": True})
    observability.configure_observability(settings)
    assert calls == [settings]


def test_trace_configuration_handles_failure(monkeypatch) -> None:
    from unittest.mock import Mock

    from job_ftch.config import Settings
    from job_ftch.infrastructure import observability
    from job_ftch.infrastructure.observability import openobserve

    monkeypatch.setattr(observability, "configure_tracing", Mock())
    monkeypatch.setattr(observability, "configure_openobserve", Mock())
    monkeypatch.setattr(
        openobserve, "_configure_safe_trace_export", Mock(side_effect=RuntimeError())
    )
    observability.configure_observability(Settings.model_validate({"tracing_enabled": True}))


def test_enabled_trace_export_unavailability_is_visible(monkeypatch, caplog) -> None:
    from job_ftch.config import Settings
    from job_ftch.infrastructure import observability
    from job_ftch.infrastructure.observability import openobserve

    monkeypatch.setattr(observability, "configure_tracing", lambda settings: None)
    monkeypatch.setattr(observability, "configure_openobserve", lambda settings: True)
    monkeypatch.setattr(openobserve, "_configure_safe_trace_export", lambda settings: False)
    observability.configure_observability(Settings.model_validate({"tracing_enabled": True}))
    assert "Safe trace export unavailable" in caplog.text


def test_safe_trace_export_over_real_otlp_http(monkeypatch) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from opentelemetry import trace
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.sdk.trace import TracerProvider
    from pydantic import SecretStr

    from job_ftch.config import Settings
    from job_ftch.infrastructure.observability.openobserve import _configure_safe_trace_export

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    settings = Settings.model_validate(
        {
            "tracing_enabled": True,
            "openobserve_enabled": True,
            "openobserve_url": f"http://127.0.0.1:{server.server_port}",
            "openobserve_username": "test",
            "openobserve_password": SecretStr("fake"),
        }
    )
    try:
        assert _configure_safe_trace_export(settings)
        with provider.get_tracer("test").start_as_current_span("pipeline.run") as span:
            span.set_attribute("job_ftch.fetched", 4)
            span.set_attribute("password", "PRIVATE")
        assert provider.force_flush()
        assert len(received) == 1 and received[0][0] == "/api/default/v1/traces"
        assert b"PRIVATE" not in received[0][1]
        request = ExportTraceServiceRequest.FromString(received[0][1])
        exported = request.resource_spans[0].scope_spans[0].spans[0]
        assert exported.name == "pipeline.run"
        assert exported.attributes[0].key == "job_ftch.fetched"
    finally:
        provider.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()


def test_openobserve_url_rejects_non_http_schemes() -> None:
    import pytest

    assert _resolve_openobserve_url("https://observe.example/") == "https://observe.example"
    with pytest.raises(ValueError, match="http or https"):
        _resolve_openobserve_url("file:///tmp/dashboard")


def test_openobserve_dashboard_copies_match_and_panels_have_descriptions() -> None:
    root = Path(__file__).parents[3]
    for name in ("job_ftch_ingest.json", "job_ftch_captcha.json"):
        packaged = root / "job_ftch/infrastructure/observability/dashboards" / name
        deploy = root / "deploy/observability/dashboards" / name
        assert packaged.read_bytes() == deploy.read_bytes()
        dashboard = json.loads(packaged.read_text(encoding="utf-8"))
        assert all(panel.get("description") for tab in dashboard["tabs"] for panel in tab["panels"])
        queries = [
            query["query"]
            for tab in dashboard["tabs"]
            for panel in tab["panels"]
            for query in panel["queries"]
        ]
        assert queries
        assert all(
            "'$source_run_id' = '' OR source_run_id = '$source_run_id'" in query
            for query in queries
        )


def test_context_filter_promotes_only_safe_correlation_fields() -> None:
    tokens = bind_contextvars(
        source_run_id="run-123",
        parent_run_id="parent-run",
        tenant_id="ai_jobs",
        graph_hash="graph-456",
        source_id="forproducts",
        ignored_payload={"large": "payload"},
    )
    try:
        record = logging.LogRecord("job_ftch", logging.INFO, __file__, 1, "event", (), None)

        assert _ContextAttributesFilter().filter(record) is True
        assert record.source_run_id == "run-123"  # type: ignore[attr-defined]
        assert getattr(record, "parent_run_id", None) == "parent-run"
        assert record.tenant_id == "ai_jobs"  # type: ignore[attr-defined]
        assert record.graph_hash == "graph-456"  # type: ignore[attr-defined]
        assert record.source_id == "forproducts"  # type: ignore[attr-defined]
        assert not hasattr(record, "ignored_payload")
    finally:
        reset_contextvars(**tokens)


def test_operational_counter_helpers_fail_closed_on_malformed_runtime_stats() -> None:
    assert _counter_value("7") == 7
    assert _counter_value("invalid") == 0
    assert _counter_value({"not": "a count"}) == 0
    assert _counter_mapping({"accept": 7}) == {"accept": 7}
    assert _counter_mapping([("accept", 7)]) == {}


def test_metric_labels_do_not_include_unique_run_id() -> None:
    from job_ftch.infrastructure.observability.openobserve import _run_attrs

    attrs = _run_attrs(RunSummary(tenant_id="ai_jobs", source_run_id="run-123"))
    assert attrs["tenant_id"] == "ai_jobs"
    assert "source_run_id" not in attrs


def test_runtime_state_metrics_snapshot_exposes_source_and_publish_state(monkeypatch) -> None:
    from job_ftch.infrastructure.observability import openobserve

    monkeypatch.setattr(openobserve, "_configured", True)
    monkeypatch.setattr(openobserve, "force_flush_openobserve", lambda: None)
    _runtime_state_snapshots.clear()
    now = datetime.now(UTC)
    summary = RunSummary(
        tenant_id="ai_jobs",
        source_run_id="run-123",
        finished_at=now,
        failed=0,
    )
    health = SourceHealth(
        source_id="career_site:example",
        source_kind="career_site",
        source_name="example",
        last_run_at=now.isoformat(),
        last_success_at=(now - timedelta(seconds=30)).isoformat(),
        failure_streak=2,
        success_count=3,
        last_fetched=4,
        last_emitted=1,
        last_failed=0,
        last_quarantined=0,
        baseline_emitted=3.0,
        drift_ratio=0.33,
        degraded=True,
        status="degraded",
    )

    record_runtime_state_metrics(
        summary,
        source_health=[health],
        scheduler_state={
            "bot_scheduler:last_attempt_at": (now - timedelta(seconds=20)).isoformat(),
            "bot_scheduler:last_success_at": (now - timedelta(seconds=10)).isoformat(),
            "bot_scheduler:last_publish_success_at": (now - timedelta(seconds=5)).isoformat(),
            "bot_scheduler:pending_publish_since": "",
            "bot_scheduler:last_publish_error": "rate limit",
            "bot_scheduler:last_publish_sent": "7",
        },
    )

    assert _runtime_state_snapshots["job_ftch.source.health.degraded"][0][0] == 1.0
    assert _runtime_state_snapshots["job_ftch.source.health.failure_streak"][0][0] == 2.0
    assert _runtime_state_snapshots["job_ftch.source.quality.reliable"][0][0] == 0.0
    assert _runtime_state_snapshots["job_ftch.source.quality.high_relevance"][0][0] == 0.0
    assert _runtime_state_snapshots["job_ftch.source.quality.important"][0][0] == 0.0
    assert _runtime_state_snapshots["job_ftch.bot.scheduler.publish_error_present"][0][0] == 1.0
    assert _runtime_state_snapshots["job_ftch.bot.scheduler.last_publish_sent"][0][0] == 7.0
