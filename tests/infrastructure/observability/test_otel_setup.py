from __future__ import annotations

from contextlib import nullcontext

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


def test_final_run_trace_is_a_noop_without_external_exporter() -> None:
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


def test_item_decision_trace_records_geo_normalization(monkeypatch) -> None:
    span = _Span()
    tracer = _Tracer(span)
    monkeypatch.setattr(
        "job_ftch.application.item_decision_trace.trace.get_tracer", lambda _: tracer
    )
    summary = RunSummary(tenant_id="ai_jobs", source_run_id="run-geo")
    record = JobRecord(
        raw_item_id="raw-geo",
        source_kind=SourceKind.CAREER_SITE,
        source_name="justjoin",
        title="ML Engineer",
        location="Варшава, Польша",
        city="Варшава",
        country="Польша",
        metadata={
            "geo_normalized_location": "Варшава, Польша",
            "geo_normalized_city": "Варшава",
            "geo_normalized_country": "Польша",
            "geo_normalization_steps": ("country:Россия->Польша",),
        },
    )

    record_item_decision_trace(
        summary=summary,
        result={
            "item": record,
            "item_id": record.stable_id,
            "current": record,
            "source_kind": record.source_kind,
            "source_name": record.source_name,
            "outcome": "emitted",
        },
        final_status="ACCEPT",
    )

    assert span.attributes["job_ftch.geo.location"] == "Варшава, Польша"
    assert span.attributes["job_ftch.geo.city"] == "Варшава"
    assert span.attributes["job_ftch.geo.country"] == "Польша"
    assert span.attributes["job_ftch.geo.normalized_location"] == "Варшава, Польша"
    assert span.attributes["job_ftch.geo.normalized_country"] == "Польша"
    assert span.attributes["job_ftch.geo.normalization_steps"] == '["country:Россия->Польша"]'


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
