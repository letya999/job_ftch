"""Per-item terminal decision trace for configured OTel exporters."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from job_ftch.application.pipeline import RunSummary


_MAX_ATTR_LEN = 4096
logger = logging.getLogger(__name__)


def record_item_decision_trace(
    *,
    summary: RunSummary,
    result: Mapping[str, Any],
    final_status: str,
    drop_reason: str | None = None,
    drop_stage: str | None = None,
) -> None:
    """Record one fetched/candidate item decision as a Langfuse-visible OTel span.

    The payload intentionally avoids raw vacancy text and full ontology payloads.
    It carries stable IDs and compact diagnostics needed to join external labels
    back to production decisions for precision/recall/F1 evaluation.
    """
    try:
        _record_item_decision_trace(
            summary=summary,
            result=result,
            final_status=final_status,
            drop_reason=drop_reason,
            drop_stage=drop_stage,
        )
    except Exception:
        logger.warning("item_decision_trace_failed", exc_info=True)


def _record_item_decision_trace(
    *,
    summary: RunSummary,
    result: Mapping[str, Any],
    final_status: str,
    drop_reason: str | None,
    drop_stage: str | None,
) -> None:
    item = result.get("item")
    current = result.get("current")
    subject = current or item
    metadata = _metadata(subject) or _metadata(item)
    node_events = result.get("graph_node_events")
    terminal_event = _terminal_event(node_events)
    llm_relevance = metadata.get("_llm_relevance") if isinstance(metadata, dict) else None
    llm_primary = llm_relevance.get("primary") if isinstance(llm_relevance, dict) else None

    tracer = trace.get_tracer("job_ftch.pipeline")
    with tracer.start_as_current_span("pipeline.item.decision") as span:
        _set(span, "job_ftch.trace_kind", "item_decision")
        _set(span, "job_ftch.source_run_id", summary.source_run_id or "")
        _set(span, "job_ftch.tenant_id", summary.tenant_id or "")
        _set(span, "job_ftch.applied_profile", summary.applied_profile or "")
        _set(span, "job_ftch.graph_hash", summary.graph_hash or "")
        _set(span, "job_ftch.item_id", result.get("item_id") or getattr(subject, "stable_id", ""))
        _set(span, "job_ftch.raw_item_id", getattr(subject, "raw_item_id", ""))
        _set(span, "job_ftch.source_record_id", getattr(subject, "source_record_id", ""))
        _set(span, "job_ftch.source_kind", _enum_value(result.get("source_kind")))
        _set(span, "job_ftch.source_name", result.get("source_name") or "")
        _set(span, "job_ftch.final_status", final_status.upper())
        _set(span, "job_ftch.pipeline_outcome", result.get("outcome") or "")
        _set(span, "job_ftch.drop_reason", drop_reason or "")
        _set(span, "job_ftch.drop_stage", drop_stage or "")

        routing_decision = getattr(subject, "routing_decision", None)
        if routing_decision is not None:
            _set(span, "job_ftch.routing_decision", _enum_value(routing_decision))
        _set(span, "job_ftch.best_profile_id", getattr(subject, "best_profile_id", ""))
        _set(span, "job_ftch.best_score", getattr(subject, "best_score", None))
        _set(span, "job_ftch.relevance_score", getattr(subject, "relevance_score", None))
        _set(span, "job_ftch.quality_score", getattr(subject, "quality_score", None))
        _set(span, "job_ftch.geo.location", getattr(subject, "location", ""))
        _set(span, "job_ftch.geo.city", getattr(subject, "city", ""))
        _set(span, "job_ftch.geo.country", getattr(subject, "country", ""))

        if isinstance(metadata, dict):
            _set_metadata(span, metadata)
        if isinstance(llm_relevance, dict):
            _set(span, "job_ftch.llm_relevance.decision", llm_relevance.get("decision") or "")
            _set(
                span,
                "job_ftch.llm_relevance.prompt_variant",
                llm_relevance.get("prompt_variant") or "",
            )
            _set(
                span,
                "job_ftch.llm_relevance.classification_mode",
                llm_relevance.get("classification_mode") or "",
            )
        if isinstance(llm_primary, dict):
            _set(span, "job_ftch.llm_relevance.is_job", llm_primary.get("is_job") or "")
            _set(
                span,
                "job_ftch.llm_relevance.role_relation",
                llm_primary.get("role_relation") or "",
            )
            _set(
                span,
                "job_ftch.llm_relevance.responsibility_fit",
                llm_primary.get("responsibility_fit") or "",
            )
        if isinstance(terminal_event, dict):
            _set(span, "job_ftch.terminal_node_id", terminal_event.get("node_id") or "")
            _set(span, "job_ftch.terminal_node", terminal_event.get("node") or "")
            _set(
                span,
                "job_ftch.terminal_reasons",
                _json_list(terminal_event.get("terminal_reasons")),
            )


def _set_metadata(span: Any, metadata: Mapping[str, Any]) -> None:
    _set(span, "job_ftch.relevance_prefilter.score", metadata.get("relevance_prefilter_score"))
    _set(
        span,
        "job_ftch.relevance_prefilter.threshold",
        metadata.get("relevance_prefilter_threshold"),
    )
    _set(
        span,
        "job_ftch.relevance_prefilter.decision",
        metadata.get("relevance_prefilter_decision") or "",
    )
    _set(
        span,
        "job_ftch.relevance_prefilter.model_version",
        metadata.get("relevance_prefilter_model_version") or "",
    )
    _set(
        span,
        "job_ftch.semantic_prefilter.best_profile",
        metadata.get("semantic_prefilter_best_profile") or "",
    )
    _set(
        span,
        "job_ftch.semantic_prefilter.best_score",
        metadata.get("semantic_prefilter_best_score"),
    )
    _set(span, "job_ftch.decision_reasons", _json_list(metadata.get("decision_reasons")))
    _set(span, "job_ftch.geo.normalized_location", metadata.get("geo_normalized_location") or "")
    _set(span, "job_ftch.geo.normalized_city", metadata.get("geo_normalized_city") or "")
    _set(span, "job_ftch.geo.normalized_country", metadata.get("geo_normalized_country") or "")
    _set(
        span,
        "job_ftch.geo.normalization_steps",
        _json_list(metadata.get("geo_normalization_steps")),
    )

    snapshots = metadata.get("ontology_snapshots")
    if isinstance(snapshots, Mapping):
        ids: list[str] = []
        versions: dict[str, str] = {}
        for profile_id, payload in snapshots.items():
            profile = str(profile_id)
            ids.append(profile)
            if isinstance(payload, Mapping):
                version = str(payload.get("version") or "")
                if version:
                    versions[profile] = version
        _set(span, "job_ftch.ontology_snapshot_ids", _json_list(sorted(ids)))
        _set(
            span,
            "job_ftch.ontology_snapshot_versions",
            json.dumps(versions, ensure_ascii=False, sort_keys=True),
        )


def _metadata(item: object) -> dict[str, Any]:
    metadata = getattr(item, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _terminal_event(node_events: object) -> Mapping[str, Any] | None:
    if not isinstance(node_events, Mapping):
        return None
    for event in node_events.values():
        if isinstance(event, Mapping) and str(event.get("effect") or "") == "terminal_decision":
            return event
    return None


def _json_list(value: object) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        normalized = [value] if value else []
    elif isinstance(value, Sequence):
        normalized = [str(item) for item in value if item is not None]
    else:
        normalized = [str(value)]
    return json.dumps(normalized, ensure_ascii=False)


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _set(span: Any, key: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, (bool, int, float)):
        span.set_attribute(key, value)
        return
    text = str(value)
    if len(text) > _MAX_ATTR_LEN:
        text = f"{text[:_MAX_ATTR_LEN]}...[truncated]"
    span.set_attribute(key, text)
