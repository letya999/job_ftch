"""Build per-run relational stats from a finished RunSummary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from job_ftch.application.source_quality import FAIL_STATUSES, canonical_source_key
from job_ftch.domain.run_stats import PipelineRunStats, SourceRunStatsRow

if TYPE_CHECKING:
    from collections.abc import Mapping

    from job_ftch.application.pipeline import RunSummary
    from job_ftch.application.source_quality import SourceQualityStats


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _duration_ms(summary: RunSummary) -> int:
    started = summary.started_at
    finished = summary.finished_at
    if finished is None:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return max(int((finished - started).total_seconds() * 1000), 0)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def build_pipeline_run_stats(summary: RunSummary) -> PipelineRunStats:
    outcomes = [item for item in (summary.source_outcomes or []) if isinstance(item, dict)]
    fail_sources = 0
    ok_sources = 0
    seen: set[str] = set()
    for outcome in outcomes:
        key = canonical_source_key(
            str(outcome.get("source_id") or ""),
            str(outcome.get("source_name") or ""),
        )
        if key in seen or key == "unknown":
            continue
        seen.add(key)
        status = str(outcome.get("status") or "unknown")
        if status in FAIL_STATUSES:
            fail_sources += 1
        else:
            ok_sources += 1
    extra = {
        "by_source_kind": {
            kind: {
                "fetched": stats.fetched,
                "extracted": stats.extracted,
                "emitted": stats.emitted,
                "failed": stats.failed,
            }
            for kind, stats in summary.by_source_kind.items()
        },
        "drop_reasons": dict(summary.drop_reasons),
    }
    return PipelineRunStats(
        source_run_id=str(summary.source_run_id or ""),
        started_at=_iso(summary.started_at) or datetime.now(UTC).isoformat(),
        finished_at=_iso(summary.finished_at),
        duration_ms=_duration_ms(summary),
        source_count=len(seen),
        ok_sources=ok_sources,
        fail_sources=fail_sources,
        fetched=summary.fetched,
        extracted=summary.extracted,
        emitted=summary.emitted,
        review=summary.review,
        rejected=summary.rejected,
        dropped=summary.dropped,
        failed=summary.failed,
        duplicates=summary.duplicates,
        llm_calls=summary.llm_usage_requests,
        llm_tokens_in=summary.llm_tokens_in,
        llm_tokens_out=summary.llm_tokens_out,
        llm_latency_ms=summary.llm_latency_ms,
        llm_cost_usd=float(summary.llm_cost_usd or 0.0),
        conversion_extract=_ratio(summary.extracted, summary.fetched),
        conversion_accept=_ratio(summary.emitted, summary.fetched),
        extra_json=json.dumps(extra, ensure_ascii=False, sort_keys=True, default=str),
    )


def build_source_run_stats(
    summary: RunSummary,
    *,
    quality: Mapping[str, SourceQualityStats] | None = None,
    important: Mapping[str, bool] | None = None,
) -> list[SourceRunStatsRow]:
    quality = quality or {}
    important = important or {}
    by_id = summary.by_source_id or {}
    started = _iso(summary.started_at) or datetime.now(UTC).isoformat()
    finished = _iso(summary.finished_at)
    rows: dict[str, SourceRunStatsRow] = {}
    for outcome in summary.source_outcomes or []:
        if not isinstance(outcome, dict):
            continue
        source_id = str(outcome.get("source_id") or "").strip()
        if not source_id:
            continue
        key = canonical_source_key(source_id, str(outcome.get("source_name") or ""))
        identity = by_id.get(source_id)
        fetched = int(getattr(identity, "fetched", 0) or 0)
        emitted = int(getattr(identity, "emitted", 0) or 0)
        extracted = int(getattr(identity, "extracted", 0) or 0)
        dropped = int(getattr(identity, "dropped", 0) or 0)
        failed = int(getattr(identity, "failed", 0) or 0)
        labels = quality.get(key)
        denom = fetched or int(str(outcome.get("yielded") or 0))
        rows[source_id] = SourceRunStatsRow(
            source_run_id=str(summary.source_run_id or ""),
            source_id=source_id,
            source_key=key,
            source_kind=str(outcome.get("source_kind") or source_id.partition(":")[0] or "unknown"),
            source_name=str(outcome.get("source_name") or key),
            status=str(outcome.get("status") or "unknown"),
            started_at=started,
            finished_at=finished,
            yielded=int(str(outcome.get("yielded") or 0)),
            fetched=fetched,
            extracted=extracted,
            emitted=emitted,
            dropped=dropped,
            failed=failed,
            duration_ms=0,
            llm_latency_ms=int(getattr(identity, "llm_latency_ms", 0) or 0),
            llm_cost_usd=float(getattr(identity, "llm_cost_usd", 0.0) or 0.0),
            conversion_accept=_ratio(emitted, denom),
            quality_reliable=bool(labels.reliable) if labels else False,
            quality_rich=bool(labels.rich) if labels else False,
            quality_high_relevance=bool(labels.high_relevance) if labels else False,
            quality_important=bool(important.get(key, False)),
            error=str(outcome.get("error") or "") or None,
        )
    return list(rows.values())
