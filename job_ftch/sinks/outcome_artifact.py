"""Compact projections for operational REVIEW / REJECTED lanes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from job_ftch.application.contracts import Sink

_REVIEW_DESCRIPTION_LIMIT = 6_000
_REJECTED_DESCRIPTION_LIMIT = 4_000
_DETAILS_LIMIT = 1_000
_TRACE_KEEP = frozenset(
    {
        "source_run_id",
        "run_id",
        "drop_reason",
        "drop_stage",
        "node",
        "stage",
    }
)


def compact_review_payload(item: object) -> dict[str, Any]:
    """Project a JobRecord (or mapping) into a compact review row."""
    payload: Mapping[str, Any]
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = item
    else:
        payload = {"value": str(item)}

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    description = str(
        payload.get("description_raw")
        or payload.get("description")
        or metadata.get("original_posting_text")
        or ""
    )
    return {
        "lane": "review",
        "stable_id": payload.get("stable_id"),
        "raw_item_id": payload.get("raw_item_id"),
        "source_run_id": metadata.get("source_run_id") or payload.get("source_run_id"),
        "source_kind": payload.get("source_kind"),
        "source_name": payload.get("source_name"),
        "source_record_id": payload.get("source_record_id"),
        "title": payload.get("title"),
        "company": payload.get("company"),
        "canonical_url": payload.get("canonical_url"),
        "posted_at": payload.get("posted_at"),
        "post_type": payload.get("post_type"),
        "routing_decision": payload.get("routing_decision"),
        "role_family": payload.get("role_family"),
        "role_specialization": payload.get("role_specialization"),
        "seniority": payload.get("seniority"),
        "extraction_status": payload.get("extraction_status"),
        "quality_score": payload.get("quality_score"),
        "relevance_score": payload.get("relevance_score"),
        "best_score": payload.get("best_score"),
        "review_reasons": payload.get("review_reasons") or [],
        "decision_reasons": metadata.get("decision_reasons") or [],
        "llm_relevance": metadata.get("_llm_relevance"),
        "source_context": metadata.get("source_context"),
        "source_family": metadata.get("source_family"),
        "observation_kind": metadata.get("observation_kind"),
        "transport": metadata.get("transport"),
        "description_excerpt": description[:_REVIEW_DESCRIPTION_LIMIT],
        "description_truncated": len(description) > _REVIEW_DESCRIPTION_LIMIT,
    }


def compact_rejected_payload(item: object) -> dict[str, Any]:
    """Project a RejectedItem (or mapping) without the full snapshot dump."""
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = dict(item)
    else:
        payload = {"value": str(item)}

    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    metadata = snapshot.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    description = str(
        snapshot.get("description_raw")
        or snapshot.get("description")
        or snapshot.get("text")
        or metadata.get("original_posting_text")
        or ""
    )
    details = str(payload.get("details") or "")
    raw_trace = payload.get("trace")
    trace: dict[str, Any] = {}
    if isinstance(raw_trace, dict):
        for key in _TRACE_KEEP:
            if key in raw_trace and raw_trace[key] is not None:
                trace[key] = raw_trace[key]

    source_run_id = (
        trace.get("source_run_id")
        or trace.get("run_id")
        or metadata.get("source_run_id")
        or payload.get("source_run_id")
    )

    return {
        "lane": "rejected",
        "outcome": payload.get("outcome"),
        "reason": payload.get("reason"),
        "details": details[:_DETAILS_LIMIT],
        "details_truncated": len(details) > _DETAILS_LIMIT,
        "stage": payload.get("stage"),
        "item_type": payload.get("item_type"),
        "source_kind": payload.get("source_kind") or snapshot.get("source_kind"),
        "source_name": payload.get("source_name") or snapshot.get("source_name"),
        "stable_id": payload.get("stable_id") or snapshot.get("stable_id"),
        "raw_item_id": payload.get("raw_item_id") or snapshot.get("raw_item_id"),
        "recorded_at": payload.get("recorded_at"),
        "source_run_id": source_run_id,
        "title": snapshot.get("title"),
        "company": snapshot.get("company"),
        "canonical_url": snapshot.get("canonical_url"),
        "routing_decision": snapshot.get("routing_decision"),
        "relevance_score": snapshot.get("relevance_score"),
        "best_score": snapshot.get("best_score"),
        "review_reasons": snapshot.get("review_reasons") or [],
        "trace": trace,
        "description_excerpt": description[:_REJECTED_DESCRIPTION_LIMIT],
        "description_truncated": len(description) > _REJECTED_DESCRIPTION_LIMIT,
    }


class CompactReviewSink:
    """Project a JobRecord before passing it to the configured review sink."""

    def __init__(self, sink: Sink[Any]) -> None:
        self._sink = sink

    async def emit(self, item: object) -> None:
        await self._sink.emit(compact_review_payload(item))

    async def flush(self) -> None:
        flush = getattr(self._sink, "flush", None)
        if callable(flush):
            await flush()


class CompactRejectedSink:
    """Project a RejectedItem before passing it to the configured rejected sink."""

    def __init__(self, sink: Sink[Any]) -> None:
        self._sink = sink

    async def emit(self, item: object) -> None:
        await self._sink.emit(compact_rejected_payload(item))

    async def flush(self) -> None:
        flush = getattr(self._sink, "flush", None)
        if callable(flush):
            await flush()
