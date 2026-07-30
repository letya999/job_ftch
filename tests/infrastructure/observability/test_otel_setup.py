from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider
from pydantic import SecretStr

from job_ftch.application.item_decision_trace import record_item_decision_trace
from job_ftch.application.pipeline import RunSummary
from job_ftch.domain import JobRecord, MatchDecision, RawItem, SourceKind
from job_ftch.infrastructure.observability import otel_setup


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _Tracer:
    def __init__(self, span: _Span) -> None:
        self.span = span
        self.name: str | None = None

    def start_as_current_span(self, name: str):
        self.name = name
        return nullcontext(self.span)


def test_final_run_trace_uses_post_sink_summary(monkeypatch) -> None:
    span = _Span()
    tracer = _Tracer(span)
    monkeypatch.setattr(otel_setup.trace, "get_tracer", lambda _: tracer)
    summary = RunSummary(
        tenant_id="ai_jobs",
        source_run_id="run-123",
        fetched=50,
        extracted=20,
        emitted=7,
        review=4,
        rejected=9,
        deferred=2,
        new_groups_created=6,
        merged_into_group=1,
        llm_usage_requests=3,
        llm_tokens_in=120,
        llm_cached_tokens_in=20,
        llm_tokens_out=30,
        llm_cost_usd=0.012,
        llm_cost_pricing_version="pricing-v1",
    )

    otel_setup.record_final_run_trace(summary)

    assert tracer.name == "pipeline.run.final"
    assert span.attributes["job_ftch.source_run_id"] == "run-123"
    assert span.attributes["job_ftch.routing_accepted"] == 7
    assert span.attributes["job_ftch.routed_review"] == 4
    assert span.attributes["job_ftch.routed_rejected"] == 9
    assert span.attributes["job_ftch.routed_deferred"] == 2
    assert span.attributes["job_ftch.groups_created"] == 6
    assert span.attributes["job_ftch.groups_merged"] == 1
    assert span.attributes["job_ftch.llm_tokens_in"] == 120
    assert span.attributes["job_ftch.llm_cost_is_complete"] is True
    assert span.attributes["job_ftch.llm_cost_usd"] == 0.012


def test_configure_tracing_adds_processor_to_existing_sdk_provider(monkeypatch) -> None:
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as trace_exporter
    import opentelemetry.sdk.trace.export as trace_export

    class _Exporter:
        def __init__(self, endpoint: str, headers: dict[str, str]) -> None:
            self.endpoint = endpoint
            self.headers = headers

    class _Processor:
        def __init__(self, exporter: _Exporter) -> None:
            self.exporter = exporter

    provider = TracerProvider()
    processors: list[_Processor] = []
    monkeypatch.setattr(otel_setup.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(
        otel_setup.trace,
        "set_tracer_provider",
        lambda _: (_ for _ in ()).throw(AssertionError("must not replace provider")),
    )
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", _Exporter)
    monkeypatch.setattr(trace_export, "BatchSpanProcessor", _Processor)
    monkeypatch.setattr(provider, "add_span_processor", processors.append)
    otel_setup._CONFIGURED_PROVIDER_IDS.clear()
    settings = SimpleNamespace(
        tracing_enabled=True,
        langfuse_public_key="pk",
        langfuse_secret_key=SecretStr("sk"),
        langfuse_host="http://langfuse.local",
        otel_service_name="job_ftch",
    )

    otel_setup.configure_tracing(settings)  # type: ignore[arg-type]

    assert len(processors) == 1
    assert processors[0].exporter.endpoint == "http://langfuse.local/api/public/otel/v1/traces"
    assert id(provider) in otel_setup._CONFIGURED_PROVIDER_IDS


def test_item_decision_trace_records_accept_contract(monkeypatch) -> None:
    span = _Span()
    tracer = _Tracer(span)
    monkeypatch.setattr(
        "job_ftch.application.item_decision_trace.trace.get_tracer", lambda _: tracer
    )
    summary = RunSummary(
        tenant_id="ai_jobs",
        applied_profile="default",
        source_run_id="run-123",
        graph_hash="graph-hash",
    )
    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_ru",
        external_id="1",
        url="https://example.com/vacancy/1",
        text="Python engineer",
        metadata={
            "relevance_prefilter_score": 0.91,
            "relevance_prefilter_threshold": 0.2,
            "relevance_prefilter_decision": "pass",
            "relevance_prefilter_model_version": "tfidf-logreg-v1",
            "ontology_snapshots": {"p1": {"version": "abc", "payload_json": "large"}},
        },
    )
    record = JobRecord(
        raw_item_id=raw.stable_id,
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_ru",
        source_url=raw.url,
        title="Python engineer",
        routing_decision=MatchDecision.ACCEPT,
        best_profile_id="p1",
        best_score=0.88,
        metadata={
            **raw.metadata,
            "_llm_relevance": {
                "decision": "accept",
                "prompt_variant": "profile_default",
                "classification_mode": "compact_evidence",
                "primary": {
                    "is_job": "yes",
                    "role_relation": "target",
                    "responsibility_fit": "support",
                },
            },
            "decision_reasons": ("profile_relevance_confirmed",),
        },
    )

    record_item_decision_trace(
        summary=summary,
        result={
            "item": raw,
            "item_id": raw.stable_id,
            "current": record,
            "source_kind": raw.source_kind,
            "source_name": raw.source_name,
            "outcome": "emitted",
            "graph_node_events": {
                "decision": {
                    "node_id": "decision",
                    "node": "evidence_decision",
                    "effect": "terminal_decision",
                    "terminal_reasons": ["profile_relevance_confirmed"],
                }
            },
        },
        final_status="ACCEPT",
    )

    assert tracer.name == "pipeline.item.decision"
    assert span.attributes["job_ftch.trace_kind"] == "item_decision"
    assert span.attributes["job_ftch.source_run_id"] == "run-123"
    assert span.attributes["job_ftch.tenant_id"] == "ai_jobs"
    assert span.attributes["job_ftch.item_id"] == raw.stable_id
    assert span.attributes["job_ftch.raw_item_id"] == raw.stable_id
    assert span.attributes["job_ftch.source_kind"] == "career_site"
    assert span.attributes["job_ftch.final_status"] == "ACCEPT"
    assert span.attributes["job_ftch.routing_decision"] == "accept"
    assert span.attributes["job_ftch.best_profile_id"] == "p1"
    assert span.attributes["job_ftch.relevance_prefilter.score"] == 0.91
    assert span.attributes["job_ftch.llm_relevance.decision"] == "accept"
    assert span.attributes["job_ftch.llm_relevance.is_job"] == "yes"
    assert span.attributes["job_ftch.terminal_node_id"] == "decision"
    assert span.attributes["job_ftch.ontology_snapshot_ids"] == '["p1"]'
    assert span.attributes["job_ftch.ontology_snapshot_versions"] == '{"p1": "abc"}'


def test_item_decision_trace_records_prefilter_drop_as_reject(monkeypatch) -> None:
    span = _Span()
    tracer = _Tracer(span)
    monkeypatch.setattr(
        "job_ftch.application.item_decision_trace.trace.get_tracer", lambda _: tracer
    )
    summary = RunSummary(tenant_id="ai_jobs", source_run_id="run-123")
    raw = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_ru",
        external_id="1",
        text="Generic office role",
        metadata={
            "relevance_prefilter_score": 0.08,
            "relevance_prefilter_threshold": 0.2,
            "relevance_prefilter_decision": "drop",
            "relevance_prefilter_model_version": "tfidf-logreg-v1",
        },
    )

    record_item_decision_trace(
        summary=summary,
        result={
            "item": raw,
            "item_id": raw.stable_id,
            "current": None,
            "source_kind": raw.source_kind,
            "source_name": raw.source_name,
            "outcome": "dropped_node",
        },
        final_status="REJECT",
        drop_reason="low_relevance_prefilter",
        drop_stage="tfidf_logreg_prefilter",
    )

    assert span.attributes["job_ftch.final_status"] == "REJECT"
    assert span.attributes["job_ftch.drop_reason"] == "low_relevance_prefilter"
    assert span.attributes["job_ftch.drop_stage"] == "tfidf_logreg_prefilter"
    assert span.attributes["job_ftch.relevance_prefilter.decision"] == "drop"
