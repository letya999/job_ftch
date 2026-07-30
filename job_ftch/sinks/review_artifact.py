"""Compact, human-readable projection for review diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from job_ftch.application.contracts import Sink

_DESCRIPTION_LIMIT = 6_000


def compact_review_payload(item: object) -> dict[str, Any]:
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
        "stable_id": payload.get("stable_id"),
        "raw_item_id": payload.get("raw_item_id"),
        "source_run_id": metadata.get("source_run_id"),
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
        "description_excerpt": description[:_DESCRIPTION_LIMIT],
        "description_truncated": len(description) > _DESCRIPTION_LIMIT,
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
